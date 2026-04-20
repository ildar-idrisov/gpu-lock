"""GPU lock manager: per-GPU priority queues with heartbeat and graceful shutdown.

Design
------
- One `GpuQueue` per managed GPU id. Each queue has:
    - zero or one `holder` (the lease currently owning the GPU);
    - an ordered list `queue` of pending leases, sorted by
      `(priority DESC, enqueued_at ASC)` on every insert.
- A single background task per queue (`_ticker`) handles TTL expiry for the
  holder and wait-timeout expiry for queued leases. One ticker is enough:
  the earliest deadline wins, and we recompute it on every state change.

Why a ticker and not `asyncio.create_task` per deadline: a ticker survives
restoration from persistence cleanly (we just start it after load), it makes
shutdown straightforward (cancel one task per queue), and the code is small.

Shutdown
--------
`shutdown()` rejects new enqueues (see `acquire`), fires `wait_event` on every
queued lease with a "shutdown" reason so HTTP handlers return 503 to waiters,
releases active holders, and flushes persistence. The HTTP layer triggers this
on SIGTERM/SIGINT via the lifespan hook.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Callable, Iterable

from .models import Lease, Priority, new_lease_id

log = logging.getLogger(__name__)

# Sentinel reasons the queue-wait event can fire with.
GRANT = "granted"
WAIT_TIMEOUT = "wait_timeout"
SHUTDOWN = "shutdown"
CANCELLED = "cancelled"


class ShutdownError(RuntimeError):
    """Raised by acquire() when the server is shutting down."""


@dataclass
class _WaitSlot:
    lease: Lease
    event: asyncio.Event = field(default_factory=asyncio.Event)
    reason: str = ""  # filled in before event.set()


class GpuQueue:
    """FIFO-with-priority for a single GPU."""

    def __init__(self, gpu: int, on_state_change: Callable[[], None]) -> None:
        self.gpu = gpu
        self._on_state_change = on_state_change
        self._lock = asyncio.Lock()
        self._holder: Lease | None = None
        self._queue: list[_WaitSlot] = []
        self._ticker_task: asyncio.Task | None = None  # type: ignore[type-arg]
        self._wake = asyncio.Event()
        self._closed = False

    # ---- public API, called under the manager ------------------------------

    async def enqueue(self, lease: Lease) -> _WaitSlot:
        if self._closed:
            raise ShutdownError("server is shutting down")

        slot = _WaitSlot(lease=lease)
        async with self._lock:
            if self._holder is None:
                self._holder = lease
                lease.touch()
                slot.reason = GRANT
                slot.event.set()
                log.info(
                    "lease granted on enqueue",
                    extra={"event": "grant", "lease_id": lease.lease_id,
                           "owner": lease.owner, "gpu": lease.gpu,
                           "priority": lease.priority.name.lower()},
                )
            else:
                self._queue.append(slot)
                self._queue.sort(key=lambda s: (-int(s.lease.priority), s.lease.enqueued_at))
                log.info(
                    "lease queued",
                    extra={"event": "enqueue", "lease_id": lease.lease_id,
                           "owner": lease.owner, "gpu": lease.gpu,
                           "priority": lease.priority.name.lower(),
                           "position": self._position_locked(lease.lease_id)},
                )
            self._wake.set()
        self._on_state_change()
        return slot

    async def release(self, lease_id: str) -> bool:
        async with self._lock:
            if self._holder and self._holder.lease_id == lease_id:
                log.info("lease released",
                         extra={"event": "release", "lease_id": lease_id, "gpu": self.gpu})
                self._holder = None
                self._promote_locked()
                self._wake.set()
                changed = True
            else:
                changed = False
                for i, slot in enumerate(self._queue):
                    if slot.lease.lease_id == lease_id:
                        self._queue.pop(i)
                        slot.reason = CANCELLED
                        slot.event.set()
                        log.info("queued lease cancelled",
                                 extra={"event": "cancel", "lease_id": lease_id, "gpu": self.gpu})
                        changed = True
                        break
        if changed:
            self._on_state_change()
        return changed

    async def renew(self, lease_id: str, ttl: float | None) -> Lease | None:
        async with self._lock:
            if self._holder and self._holder.lease_id == lease_id:
                self._holder.touch(ttl=ttl)
                log.info("lease renewed",
                         extra={"event": "renew", "lease_id": lease_id,
                                "gpu": self.gpu, "ttl": self._holder.ttl,
                                "expires_at": self._holder.expires_at})
                self._wake.set()
                renewed = self._holder
            else:
                renewed = None
        if renewed is not None:
            self._on_state_change()
        return renewed

    def owns(self, lease_id: str) -> bool:
        if self._holder and self._holder.lease_id == lease_id:
            return True
        return any(s.lease.lease_id == lease_id for s in self._queue)

    def load(self) -> int:
        return len(self._queue) + (1 if self._holder is not None else 0)

    def holder(self) -> Lease | None:
        return self._holder

    def snapshot(self) -> dict:
        return {
            "gpu": self.gpu,
            "holder": self._holder.to_snapshot() if self._holder else None,
            "queue": [s.lease.to_snapshot() for s in self._queue],
        }

    def restore(self, data: dict) -> None:
        """Rebuild state from a snapshot. Call before starting the ticker."""
        self._holder = Lease.from_snapshot(data["holder"]) if data.get("holder") else None
        self._queue = [
            _WaitSlot(lease=Lease.from_snapshot(d)) for d in data.get("queue", [])
        ]

    def status(self) -> dict:
        now = time.time()
        holder_info = None
        if self._holder is not None:
            held_sec = (now - self._holder.granted_at) if self._holder.granted_at else 0
            holder_info = {
                "lease_id": self._holder.lease_id,
                "owner": self._holder.owner,
                "priority": self._holder.priority.name.lower(),
                "held_sec": round(held_sec, 1),
                "ttl": self._holder.ttl,
                "expires_at": self._holder.expires_at,
            }
        return {
            "gpu": self.gpu,
            "holder": holder_info,
            "queue": [
                {
                    "lease_id": s.lease.lease_id,
                    "owner": s.lease.owner,
                    "priority": s.lease.priority.name.lower(),
                    "enqueued_at": s.lease.enqueued_at,
                }
                for s in self._queue
            ],
            "queue_length": len(self._queue),
        }

    def queue_info(self) -> dict:
        return {
            "gpu": self.gpu,
            "queue_length": len(self._queue),
            "busy": self._holder is not None,
        }

    # ---- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        if self._ticker_task is None or self._ticker_task.done():
            self._ticker_task = asyncio.create_task(self._ticker())

    async def shutdown(self) -> None:
        """Stop accepting new leases and drain waiters.

        Holders are NOT force-released: if persistence is on they survive the
        restart; if persistence is off their TTL will auto-expire naturally.
        Pending HTTP waiters can't survive restart, so we notify them so their
        handlers return 503.

        The ticker task is cancelled fire-and-forget. Awaiting it here has
        proven flaky across asyncio versions (Python 3.10 `wait_for`
        cancellation can hang in some cases). The event loop reaps the
        cancelled task during its next iteration.
        """
        self._closed = True
        async with self._lock:
            for slot in self._queue:
                slot.reason = SHUTDOWN
                slot.event.set()
            self._queue.clear()
            self._wake.set()
        t = self._ticker_task
        self._ticker_task = None
        if t is not None and not t.done():
            t.cancel()
            try:
                await t
            except BaseException:
                pass

    # ---- internals (all under self._lock unless noted) ---------------------

    def _position_locked(self, lease_id: str) -> int:
        for i, s in enumerate(self._queue):
            if s.lease.lease_id == lease_id:
                return i + 1
        return 0

    def _promote_locked(self) -> None:
        if not self._queue:
            return
        slot = self._queue.pop(0)
        self._holder = slot.lease
        slot.lease.touch()
        slot.reason = GRANT
        slot.event.set()
        log.info("lease promoted",
                 extra={"event": "grant", "lease_id": slot.lease.lease_id,
                        "owner": slot.lease.owner, "gpu": self.gpu,
                        "priority": slot.lease.priority.name.lower()})

    async def _ticker(self) -> None:
        """Fires expiries: holder TTL and queue wait timeouts.

        Plain `asyncio.sleep` poll instead of the usual `wait_for(event)`
        dance — Python 3.10's `asyncio.wait_for` can swallow cancellation in
        a way that deadlocks shutdown. 1s poll precision is fine for GPU
        leases measured in seconds to hours.
        """
        while True:
            await asyncio.sleep(1.0)
            await self._expire_now()

    async def _expire_now(self) -> None:
        now = time.time()
        state_changed = False
        async with self._lock:
            # Holder TTL
            if (
                self._holder is not None
                and self._holder.expires_at is not None
                and self._holder.expires_at <= now
            ):
                log.warning("lease auto-released on ttl",
                            extra={"event": "ttl_expire",
                                   "lease_id": self._holder.lease_id,
                                   "gpu": self.gpu})
                self._holder = None
                self._promote_locked()
                state_changed = True
            # Queue wait-timeout
            survivors: list[_WaitSlot] = []
            for s in self._queue:
                deadline = s.lease.enqueued_at + s.lease.wait_timeout
                if deadline <= now:
                    s.reason = WAIT_TIMEOUT
                    s.event.set()
                    state_changed = True
                    log.warning("queued lease timed out in wait",
                                extra={"event": "wait_timeout",
                                       "lease_id": s.lease.lease_id,
                                       "gpu": self.gpu})
                else:
                    survivors.append(s)
            self._queue = survivors
        if state_changed:
            self._on_state_change()


class GpuLockManager:
    """Fleet of per-GPU queues + dispatch."""

    def __init__(self, gpu_ids: Iterable[int], on_state_change: Callable[[], None] | None = None) -> None:
        self.gpu_ids = list(gpu_ids)
        self._on_state_change = on_state_change or (lambda: None)
        self._queues: dict[int, GpuQueue] = {
            gid: GpuQueue(gid, self._on_state_change) for gid in self.gpu_ids
        }
        self._closed = False

    # ---- setup / teardown --------------------------------------------------

    def start(self) -> None:
        for q in self._queues.values():
            q.start()

    async def shutdown(self) -> None:
        # Sequential shutdown. `asyncio.gather` here deadlocks in a way we
        # couldn't narrow down — cancellation of the ticker task inside a
        # gathered coroutine hangs. Queue count is tiny (one per GPU), so
        # sequential is fine.
        self._closed = True
        for q in self._queues.values():
            await q.shutdown()

    def is_closed(self) -> bool:
        return self._closed

    # ---- dispatch ----------------------------------------------------------

    def resolve_gpu(self, requested: str) -> int:
        if requested == "auto":
            return min(self.gpu_ids, key=lambda i: (self._queues[i].load(), i))
        try:
            gid = int(requested)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid gpu param: {requested!r}. Expected int or 'auto'.") from exc
        if gid not in self._queues:
            raise KeyError(gid)
        return gid

    async def acquire(
        self,
        owner: str,
        gpu: str,
        ttl: float,
        wait_timeout: float,
        priority: Priority,
    ) -> tuple[Lease, _WaitSlot]:
        if self._closed:
            raise ShutdownError("server is shutting down")
        gid = self.resolve_gpu(gpu)
        lease = Lease(
            lease_id=new_lease_id(),
            owner=owner,
            gpu=gid,
            priority=priority,
            ttl=ttl,
            wait_timeout=wait_timeout,
        )
        slot = await self._queues[gid].enqueue(lease)
        return lease, slot

    async def release(self, lease_id: str) -> bool:
        for q in self._queues.values():
            if q.owns(lease_id):
                return await q.release(lease_id)
        return False

    async def renew(self, lease_id: str, ttl: float | None) -> Lease | None:
        for q in self._queues.values():
            if q.owns(lease_id):
                return await q.renew(lease_id, ttl)
        return None

    # ---- reporting ---------------------------------------------------------

    def status_all(self) -> dict:
        return {
            "gpus": {str(i): q.status() for i, q in self._queues.items()},
            "gpu_ids": self.gpu_ids,
        }

    def queue_all(self) -> dict:
        return {
            "gpus": {str(i): q.queue_info() for i, q in self._queues.items()},
            "gpu_ids": self.gpu_ids,
        }

    def single(self, gpu: int) -> GpuQueue:
        return self._queues[gpu]

    # ---- persistence -------------------------------------------------------

    def snapshot(self) -> dict:
        return {
            "version": 1,
            "gpu_ids": self.gpu_ids,
            "queues": {str(i): q.snapshot() for i, q in self._queues.items()},
        }

    def restore(self, data: dict) -> None:
        for gid_str, qdata in data.get("queues", {}).items():
            gid = int(gid_str)
            if gid in self._queues:
                self._queues[gid].restore(qdata)
