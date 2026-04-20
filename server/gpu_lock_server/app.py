"""FastAPI app factory.

Endpoints
---------
    POST /acquire?owner=&gpu=&ttl=&wait_timeout=&priority=   → Lease | 503
    POST /release/{lease_id}                                  → {"ok": true}
    POST /renew/{lease_id}?ttl=                               → Lease
    GET  /status[?gpu=]                                       → per-GPU state
    GET  /queue[?gpu=]                                        → queue stats
    GET  /health                                              → {"status":"ok"}

The `acquire` flow:
    1. Build a lease, enqueue it on the target GPU.
    2. If the queue was empty it's granted immediately (slot event already set).
    3. Otherwise wait on the slot's event up to `wait_timeout`.
    4. On `granted`, return the lease; on `wait_timeout`/`shutdown`/`cancelled`,
       return the appropriate HTTP error.

Graceful shutdown drains waiters through the manager's `shutdown()`, which
fires every wait event with reason `shutdown`. The acquire handler sees that
and returns 503.
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from . import __version__
from .auth import AuthMiddleware
from .config import Settings
from .logging_config import configure as configure_logging
from .manager import GRANT, SHUTDOWN, WAIT_TIMEOUT, GpuLockManager, ShutdownError
from .models import Priority
from .persistence import StateFile

log = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    configure_logging(level=settings.log_level, file=settings.log_file)

    state = StateFile(settings.state_file)
    manager = GpuLockManager(settings.gpu_ids, on_state_change=lambda: _flush(state, manager))

    saved = state.load()
    if saved:
        manager.restore(saved)
        log.info("state restored", extra={"event": "state_restored",
                                          "path": str(state.path)})

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        manager.start()
        log.info("server started", extra={"event": "startup",
                                          "gpu_ids": settings.gpu_ids,
                                          "auth_enabled": settings.auth_token is not None,
                                          "state_file": str(state.path) if state.path else None})
        try:
            yield
        finally:
            log.info("server stopping — graceful shutdown in progress",
                     extra={"event": "shutdown_begin"})
            try:
                await asyncio.wait_for(
                    manager.shutdown(),
                    timeout=settings.shutdown_drain_seconds,
                )
            except asyncio.TimeoutError:
                log.error("shutdown drain exceeded timeout",
                          extra={"event": "shutdown_drain_timeout"})
            _flush(state, manager)
            log.info("server stopped", extra={"event": "shutdown_done"})

    app = FastAPI(
        title="gpu-lock",
        version=__version__,
        lifespan=lifespan,
    )
    app.add_middleware(AuthMiddleware, token=settings.auth_token)

    @app.exception_handler(ValueError)
    async def _value_error(_: Request, exc: ValueError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.post("/acquire")
    async def acquire(
        owner: str = Query(default="unknown"),
        gpu: str = Query(..., description="GPU id or 'auto'"),
        ttl: float = Query(default=300, ge=10, le=86400,
                           description="Seconds the lease stays valid. Renew to extend."),
        wait_timeout: float = Query(default=300, ge=1, le=86400,
                                    description="Seconds the client is willing to wait in queue."),
        priority: str = Query(default="normal",
                              description="low|normal|high|immediate"),
    ):
        try:
            prio = Priority.parse(priority)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        try:
            lease, slot = await manager.acquire(owner, gpu, ttl, wait_timeout, prio)
        except KeyError:
            raise HTTPException(404, f"GPU {gpu} not managed. Available: {settings.gpu_ids}")
        except ShutdownError:
            raise HTTPException(503, "server is shutting down")

        if not slot.event.is_set():
            try:
                await asyncio.wait_for(slot.event.wait(), timeout=wait_timeout + 1)
            except asyncio.TimeoutError:
                # Ticker should have set wait_timeout already, but guard anyway.
                await manager.release(lease.lease_id)
                raise HTTPException(408, f"Timed out waiting for GPU {lease.gpu} ({wait_timeout}s)")

        if slot.reason == GRANT:
            return lease.to_public()
        if slot.reason == WAIT_TIMEOUT:
            raise HTTPException(408, f"Timed out waiting for GPU {lease.gpu} ({wait_timeout}s)")
        if slot.reason == SHUTDOWN:
            raise HTTPException(503, "server is shutting down")
        raise HTTPException(500, f"unexpected wait reason: {slot.reason!r}")

    @app.post("/release/{lease_id}")
    async def release(lease_id: str):
        ok = await manager.release(lease_id)
        if not ok:
            raise HTTPException(404, f"Lease {lease_id} not found")
        return {"ok": True}

    @app.post("/renew/{lease_id}")
    async def renew(
        lease_id: str,
        ttl: float | None = Query(default=None, ge=10, le=86400,
                                  description="New TTL. If omitted, extends by the previous TTL."),
    ):
        lease = await manager.renew(lease_id, ttl)
        if lease is None:
            raise HTTPException(404, f"Lease {lease_id} not held")
        return lease.to_public()

    @app.get("/status")
    async def status(gpu: int | None = Query(default=None)):
        if gpu is None:
            return manager.status_all()
        try:
            return manager.single(gpu).status()
        except KeyError:
            raise HTTPException(404, f"GPU {gpu} not managed. Available: {settings.gpu_ids}")

    @app.get("/queue")
    async def queue(gpu: int | None = Query(default=None)):
        if gpu is None:
            return manager.queue_all()
        try:
            return manager.single(gpu).queue_info()
        except KeyError:
            raise HTTPException(404, f"GPU {gpu} not managed. Available: {settings.gpu_ids}")

    @app.get("/health")
    async def health():
        return {
            "status": "ok",
            "gpu_ids": settings.gpu_ids,
            "version": __version__,
            "shutting_down": manager.is_closed(),
        }

    return app


def _flush(state: StateFile, manager: GpuLockManager) -> None:
    if not state.enabled:
        return
    try:
        state.write(manager.snapshot())
    except OSError as exc:
        log.error("state flush failed",
                  extra={"event": "state_write_error", "error": str(exc)})


# For `uvicorn gpu_lock_server.app:app`
app = create_app()
