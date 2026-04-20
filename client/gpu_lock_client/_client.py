"""HTTP client for the gpu-lock service — async and sync.

Passthrough mode
----------------
If `GPU_LOCK_URL` is empty the client becomes a no-op: acquire returns None,
context managers yield None, release/renew do nothing. This keeps single-user
dev workflows simple (same code runs with and without the service).

Auth
----
If `GPU_LOCK_TOKEN` is set it is sent as `Authorization: Bearer <token>` on
every request.

Heartbeat
---------
The context managers (`gpu_lock`, `gpu_lock_sync`) start a background task/
thread that renews the lease at ~`ttl/3` intervals until the context exits.
For callers who prefer manual control: `acquire_*` + `renew_*` + `release_*`.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from contextlib import asynccontextmanager, contextmanager
from typing import Any, Optional

from ._types import GpuSpec, Lease, Priority, PriorityLike

log = logging.getLogger(__name__)


def _url() -> str:
    return os.environ.get("GPU_LOCK_URL", "")


def _token() -> str | None:
    t = os.environ.get("GPU_LOCK_TOKEN", "")
    return t or None


def _headers() -> dict[str, str]:
    t = _token()
    return {"Authorization": f"Bearer {t}"} if t else {}


def _enabled() -> bool:
    return bool(_url())


def _priority_value(p: PriorityLike) -> str:
    return p.value if isinstance(p, Priority) else Priority(p).value


# ---------------------------------------------------------------------------
# Queue introspection — cheap call for "is there even a point in waiting?"
# ---------------------------------------------------------------------------

async def queue_info_async(gpu: Optional[int] = None) -> Optional[dict[str, Any]]:
    if not _enabled():
        return None
    import httpx
    try:
        async with httpx.AsyncClient(timeout=5, headers=_headers()) as client:
            params = {"gpu": str(gpu)} if gpu is not None else {}
            r = await client.get(f"{_url()}/queue", params=params)
            r.raise_for_status()
            return r.json()
    except Exception as exc:
        log.warning("gpu-lock queue_info failed (%s)", exc)
        return None


def queue_info_sync(gpu: Optional[int] = None) -> Optional[dict[str, Any]]:
    if not _enabled():
        return None
    import httpx
    try:
        with httpx.Client(timeout=5, headers=_headers()) as client:
            params = {"gpu": str(gpu)} if gpu is not None else {}
            r = client.get(f"{_url()}/queue", params=params)
            r.raise_for_status()
            return r.json()
    except Exception as exc:
        log.warning("gpu-lock queue_info failed (%s)", exc)
        return None


# ---------------------------------------------------------------------------
# Acquire / release / renew — low-level
# ---------------------------------------------------------------------------

async def acquire_async(
    owner: str,
    gpu: GpuSpec,
    ttl: float = 300,
    wait_timeout: float = 300,
    priority: PriorityLike = Priority.NORMAL,
) -> Optional[Lease]:
    if not _enabled():
        return None
    import httpx
    params = {
        "owner": owner,
        "gpu": str(gpu),
        "ttl": ttl,
        "wait_timeout": wait_timeout,
        "priority": _priority_value(priority),
    }
    try:
        async with httpx.AsyncClient(timeout=wait_timeout + 5, headers=_headers()) as client:
            r = await client.post(f"{_url()}/acquire", params=params)
            r.raise_for_status()
            lease = Lease.from_response(r.json())
            log.info("GPU acquired: owner=%s lease=%s gpu=%s priority=%s",
                     owner, lease.lease_id, lease.gpu, lease.priority.value)
            return lease
    except Exception as exc:
        log.warning("gpu-lock acquire failed (%s), proceeding without lock", exc)
        return None


def acquire_sync(
    owner: str,
    gpu: GpuSpec,
    ttl: float = 300,
    wait_timeout: float = 300,
    priority: PriorityLike = Priority.NORMAL,
) -> Optional[Lease]:
    if not _enabled():
        return None
    import httpx
    params = {
        "owner": owner,
        "gpu": str(gpu),
        "ttl": ttl,
        "wait_timeout": wait_timeout,
        "priority": _priority_value(priority),
    }
    try:
        with httpx.Client(timeout=wait_timeout + 5, headers=_headers()) as client:
            r = client.post(f"{_url()}/acquire", params=params)
            r.raise_for_status()
            lease = Lease.from_response(r.json())
            log.info("GPU acquired: owner=%s lease=%s gpu=%s priority=%s",
                     owner, lease.lease_id, lease.gpu, lease.priority.value)
            return lease
    except Exception as exc:
        log.warning("gpu-lock acquire failed (%s), proceeding without lock", exc)
        return None


async def release_async(lease: Optional[Lease]) -> None:
    if lease is None or not _enabled():
        return
    import httpx
    try:
        async with httpx.AsyncClient(timeout=10, headers=_headers()) as client:
            await client.post(f"{_url()}/release/{lease.lease_id}")
        log.info("GPU released: lease=%s", lease.lease_id)
    except Exception as exc:
        log.warning("gpu-lock release failed (%s)", exc)


def release_sync(lease: Optional[Lease]) -> None:
    if lease is None or not _enabled():
        return
    import httpx
    try:
        with httpx.Client(timeout=10, headers=_headers()) as client:
            client.post(f"{_url()}/release/{lease.lease_id}")
        log.info("GPU released: lease=%s", lease.lease_id)
    except Exception as exc:
        log.warning("gpu-lock release failed (%s)", exc)


async def renew_async(lease: Optional[Lease], ttl: Optional[float] = None) -> Optional[Lease]:
    if lease is None or not _enabled():
        return None
    import httpx
    params: dict[str, Any] = {}
    if ttl is not None:
        params["ttl"] = ttl
    try:
        async with httpx.AsyncClient(timeout=10, headers=_headers()) as client:
            r = await client.post(f"{_url()}/renew/{lease.lease_id}", params=params)
            r.raise_for_status()
            return Lease.from_response(r.json())
    except Exception as exc:
        log.warning("gpu-lock renew failed (%s)", exc)
        return None


def renew_sync(lease: Optional[Lease], ttl: Optional[float] = None) -> Optional[Lease]:
    if lease is None or not _enabled():
        return None
    import httpx
    params: dict[str, Any] = {}
    if ttl is not None:
        params["ttl"] = ttl
    try:
        with httpx.Client(timeout=10, headers=_headers()) as client:
            r = client.post(f"{_url()}/renew/{lease.lease_id}", params=params)
            r.raise_for_status()
            return Lease.from_response(r.json())
    except Exception as exc:
        log.warning("gpu-lock renew failed (%s)", exc)
        return None


# ---------------------------------------------------------------------------
# Context managers with background heartbeat
# ---------------------------------------------------------------------------

def _heartbeat_interval(ttl: float) -> float:
    """Renew at ~1/3 TTL with a floor of 10s and a ceiling of 5 min."""
    return max(10.0, min(ttl / 3.0, 300.0))


@asynccontextmanager
async def gpu_lock(
    owner: str,
    gpu: GpuSpec,
    ttl: float = 300,
    wait_timeout: float = 300,
    priority: PriorityLike = Priority.NORMAL,
    heartbeat: bool = True,
):
    import asyncio as _asyncio

    lease = await acquire_async(owner, gpu, ttl, wait_timeout, priority)

    hb_task: _asyncio.Task | None = None
    if lease is not None and heartbeat:
        interval = _heartbeat_interval(ttl)

        async def _beat():
            while True:
                try:
                    await _asyncio.sleep(interval)
                    await renew_async(lease)
                except _asyncio.CancelledError:
                    raise
                except Exception as exc:
                    log.warning("gpu-lock heartbeat error (%s)", exc)

        hb_task = _asyncio.create_task(_beat())

    try:
        yield lease
    finally:
        if hb_task is not None:
            hb_task.cancel()
            try:
                await hb_task
            except BaseException:
                pass
        await release_async(lease)


@contextmanager
def gpu_lock_sync(
    owner: str,
    gpu: GpuSpec,
    ttl: float = 300,
    wait_timeout: float = 300,
    priority: PriorityLike = Priority.NORMAL,
    heartbeat: bool = True,
):
    lease = acquire_sync(owner, gpu, ttl, wait_timeout, priority)

    stop = threading.Event()
    thread: threading.Thread | None = None
    if lease is not None and heartbeat:
        interval = _heartbeat_interval(ttl)

        def _beat() -> None:
            while not stop.wait(interval):
                try:
                    renew_sync(lease)
                except Exception as exc:
                    log.warning("gpu-lock heartbeat error (%s)", exc)

        thread = threading.Thread(target=_beat, name="gpu-lock-heartbeat", daemon=True)
        thread.start()

    try:
        yield lease
    finally:
        if thread is not None:
            stop.set()
            thread.join(timeout=5)
        release_sync(lease)
