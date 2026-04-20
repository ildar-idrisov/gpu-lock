"""Launch the server with `python -m gpu_lock_server`."""
from __future__ import annotations

import os

import uvicorn


def main() -> None:
    host = os.environ.get("GPU_LOCK_HOST", "0.0.0.0")
    port = int(os.environ.get("GPU_LOCK_PORT", "8090"))
    log_level = os.environ.get("GPU_LOCK_LOG_LEVEL", "info").lower()
    uvicorn.run(
        "gpu_lock_server.app:app",
        host=host,
        port=port,
        log_level=log_level,
        access_log=False,  # stdout is already JSON from our logger
    )


if __name__ == "__main__":
    main()
