"""gpu-lock client.

Public surface:
    from gpu_lock_client import (
        Lease, Priority,
        acquire_async, acquire_sync, release_async, release_sync,
        renew_async, renew_sync,
        gpu_lock, gpu_lock_sync,
        queue_info_async, queue_info_sync,
    )

Set `GPU_LOCK_URL` env var to enable. Empty/unset → functions return None
(passthrough mode — the client never throws, the app keeps working as if
gpu-lock didn't exist).

If `GPU_LOCK_TOKEN` is set, the client adds `Authorization: Bearer <token>`.
"""
from __future__ import annotations

from ._types import Lease, Priority, PriorityLike, GpuSpec
from ._client import (
    acquire_async,
    acquire_sync,
    gpu_lock,
    gpu_lock_sync,
    queue_info_async,
    queue_info_sync,
    release_async,
    release_sync,
    renew_async,
    renew_sync,
)

# Single source of truth: pyproject.toml. Falls back when the package is run
# directly from a git checkout without an installed dist-info.
try:
    from importlib.metadata import PackageNotFoundError, version as _pkg_version
    try:
        __version__ = _pkg_version("gpu-lock-client")
    except PackageNotFoundError:
        __version__ = "0.0.0+local"
except ImportError:  # pragma: no cover  — Python <3.8, not supported anyway
    __version__ = "0.0.0+local"

__all__ = [
    "GpuSpec",
    "Lease",
    "Priority",
    "PriorityLike",
    "__version__",
    "acquire_async",
    "acquire_sync",
    "gpu_lock",
    "gpu_lock_sync",
    "queue_info_async",
    "queue_info_sync",
    "release_async",
    "release_sync",
    "renew_async",
    "renew_sync",
]
