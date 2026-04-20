"""Shared types for the client."""
from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Any, Union


class Priority(str, enum.Enum):
    """Queue priority levels.

    - IMMEDIATE: jumps to the head of the queue (does not preempt the current
      holder). Intended for interactive / latency-critical work.
    - HIGH: before NORMAL but after IMMEDIATE.
    - NORMAL: default for everyday jobs.
    - LOW: runs only when NORMAL queue is empty — for background batches.
    """

    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    IMMEDIATE = "immediate"


PriorityLike = Union[Priority, str]
GpuSpec = Union[int, str]  # int id, or "auto"


@dataclass(frozen=True)
class Lease:
    """A GPU lease granted by the server."""

    lease_id: str
    owner: str
    gpu: int
    priority: Priority
    ttl: float
    expires_at: float | None

    @classmethod
    def from_response(cls, d: dict[str, Any]) -> "Lease":
        prio = d.get("priority", "normal")
        if isinstance(prio, str):
            prio_enum = Priority(prio)
        else:
            prio_enum = Priority.NORMAL
        return cls(
            lease_id=d["lease_id"],
            owner=d["owner"],
            gpu=int(d["gpu"]),
            priority=prio_enum,
            ttl=float(d.get("ttl", 0)),
            expires_at=d.get("expires_at"),
        )
