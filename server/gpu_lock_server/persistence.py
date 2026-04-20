"""Atomic JSON snapshot persistence for the manager state.

Why
---
Without persistence, a server restart loses all leases. Clients that were
heartbeating think they still hold the lock, while a fresh process on the
other side is free to grant the same GPU to someone else — two processes fight
for one VRAM. Persistence lets us survive restart/crash without that race.

What
----
On every state change we atomically write the full manager snapshot to disk
(temp file + rename — no torn writes). On startup we load and call
`manager.restore()`. The auto-release ticker picks up where it left off:
`expires_at` is wall-clock, so remaining TTL is preserved across restart.

If `GPU_LOCK_STATE_FILE` is not set, this is a no-op and state lives only in
memory (dev default).
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


class StateFile:
    def __init__(self, path: str | None) -> None:
        self.path = Path(path) if path else None

    @property
    def enabled(self) -> bool:
        return self.path is not None

    def load(self) -> dict[str, Any] | None:
        if self.path is None or not self.path.exists():
            return None
        try:
            with self.path.open("r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as exc:
            log.error("failed to load state file",
                      extra={"event": "state_load_error",
                             "path": str(self.path), "error": str(exc)})
            return None

    def write(self, data: dict[str, Any]) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=self.path.parent,
            prefix=f".{self.path.name}.",
            suffix=".tmp",
            delete=False,
        )
        try:
            with tmp as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp.name, self.path)
        except Exception:
            try:
                os.unlink(tmp.name)
            except OSError:
                pass
            raise
