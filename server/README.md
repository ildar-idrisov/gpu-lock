# gpu-lock-server

FastAPI service that provides a FIFO priority mutex for shared GPU access.
See the [project README](https://github.com/ildar-idrisov/gpu-lock) for usage,
protocol, and configuration.

This package is not published to PyPI — the server is distributed as a Docker
image at `ghcr.io/ildar-idrisov/gpu-lock/server`. The `pyproject.toml` here
exists so you can install it in editable mode for local development:

```bash
pip install -e ./server
python -m gpu_lock_server
```
