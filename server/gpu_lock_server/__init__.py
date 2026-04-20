"""gpu-lock server package."""
from __future__ import annotations

try:
    from importlib.metadata import PackageNotFoundError, version as _pkg_version
    try:
        __version__ = _pkg_version("gpu-lock-server")
    except PackageNotFoundError:
        __version__ = "0.0.0+local"
except ImportError:  # pragma: no cover
    __version__ = "0.0.0+local"
