"""Server configuration loaded from environment variables."""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    gpu_ids: list[int]
    auth_token: str | None
    state_file: str | None
    log_file: str | None
    log_level: str
    shutdown_drain_seconds: float

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            gpu_ids=_parse_gpu_ids(os.environ.get("GPU_IDS", "0")),
            auth_token=_nonempty(os.environ.get("GPU_LOCK_TOKEN")),
            state_file=_nonempty(os.environ.get("GPU_LOCK_STATE_FILE")),
            log_file=_nonempty(os.environ.get("GPU_LOCK_LOG_FILE")),
            log_level=os.environ.get("GPU_LOCK_LOG_LEVEL", "INFO").upper(),
            shutdown_drain_seconds=float(os.environ.get("GPU_LOCK_SHUTDOWN_DRAIN", "5")),
        )


def _nonempty(v: str | None) -> str | None:
    return v if v else None


def _parse_gpu_ids(raw: str) -> list[int]:
    ids = [int(x.strip()) for x in raw.split(",") if x.strip() != ""]
    if not ids:
        raise RuntimeError(
            "GPU_IDS env var must list at least one GPU index (e.g. GPU_IDS=0,1)"
        )
    seen: set[int] = set()
    out: list[int] = []
    for i in ids:
        if i not in seen:
            seen.add(i)
            out.append(i)
    return out
