"""Domain types shared across the server."""
from __future__ import annotations

import enum
import time
import uuid
from dataclasses import dataclass, field
from typing import Any


class Priority(int, enum.Enum):
    LOW = 0
    NORMAL = 1
    HIGH = 2
    IMMEDIATE = 3

    @classmethod
    def parse(cls, raw: str | int | None) -> "Priority":
        if raw is None or raw == "":
            return cls.NORMAL
        if isinstance(raw, int):
            try:
                return cls(raw)
            except ValueError as exc:
                raise ValueError(f"Unknown priority value: {raw}") from exc
        key = str(raw).strip().lower()
        aliases = {
            "low": cls.LOW,
            "normal": cls.NORMAL,
            "default": cls.NORMAL,
            "high": cls.HIGH,
            "immediate": cls.IMMEDIATE,
            "urgent": cls.IMMEDIATE,
        }
        if key in aliases:
            return aliases[key]
        if key.isdigit():
            return cls.parse(int(key))
        raise ValueError(
            f"Unknown priority {raw!r}. Expected low|normal|high|immediate."
        )


def new_lease_id() -> str:
    return uuid.uuid4().hex[:12]


@dataclass
class Lease:
    """A GPU lease — the token granted to one holder of a GPU.

    - `enqueued_at` is wall-clock seconds; persisted so a server restart keeps
      priority ordering stable across reloads.
    - `expires_at` is wall-clock seconds; when it passes with no renewal,
      auto-release fires. None before the lease is actually granted.
    """

    lease_id: str
    owner: str
    gpu: int
    priority: Priority
    ttl: float
    wait_timeout: float
    enqueued_at: float = field(default_factory=time.time)
    granted_at: float | None = None
    expires_at: float | None = None

    def touch(self, ttl: float | None = None) -> None:
        now = time.time()
        if ttl is not None:
            self.ttl = ttl
        self.expires_at = now + self.ttl
        if self.granted_at is None:
            self.granted_at = now

    def to_public(self) -> dict[str, Any]:
        """Representation returned to HTTP clients."""
        return {
            "lease_id": self.lease_id,
            "owner": self.owner,
            "gpu": self.gpu,
            "priority": self.priority.name.lower(),
            "ttl": self.ttl,
            "expires_at": self.expires_at,
        }

    def to_snapshot(self) -> dict[str, Any]:
        """Persistable form (JSON-safe). Events and locks are reconstructed."""
        return {
            "lease_id": self.lease_id,
            "owner": self.owner,
            "gpu": self.gpu,
            "priority": int(self.priority),
            "ttl": self.ttl,
            "wait_timeout": self.wait_timeout,
            "enqueued_at": self.enqueued_at,
            "granted_at": self.granted_at,
            "expires_at": self.expires_at,
        }

    @classmethod
    def from_snapshot(cls, d: dict[str, Any]) -> "Lease":
        return cls(
            lease_id=d["lease_id"],
            owner=d["owner"],
            gpu=int(d["gpu"]),
            priority=Priority(int(d["priority"])),
            ttl=float(d["ttl"]),
            wait_timeout=float(d["wait_timeout"]),
            enqueued_at=float(d["enqueued_at"]),
            granted_at=d.get("granted_at"),
            expires_at=d.get("expires_at"),
        )
