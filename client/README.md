# gpu-lock-client

Python client + CLI for the [gpu-lock](https://github.com/ildar-idrisov/gpu-lock) service — a FIFO priority mutex that coordinates shared GPU access between processes and Docker containers.

```bash
pip install gpu-lock-client
```

## Quick use — CLI

```bash
export GPU_LOCK_URL=http://192.168.10.13:8090

# Acquire GPU 0, run a command, release on exit (sets CUDA_VISIBLE_DEVICES).
gpu-lock run --gpu 0 -- python train.py

# Let the server pick the least-busy GPU.
gpu-lock run --gpu auto -- bash

# Interactive priorities run ahead of the normal queue.
gpu-lock run --gpu auto --priority high -- python infer.py

# Low-priority batch work yields to everyone else.
gpu-lock run --gpu 0 --priority low -- python nightly_job.py

# Inspection
gpu-lock status
gpu-lock queue --gpu 0
```

## Quick use — Python

```python
from gpu_lock_client import gpu_lock, gpu_lock_sync, Priority

# Async
async with gpu_lock("my-service", gpu=0, ttl=300, priority=Priority.HIGH) as lease:
    if lease:
        os.environ["CUDA_VISIBLE_DEVICES"] = str(lease.gpu)
    ...

# Sync
with gpu_lock_sync("my-service", gpu="auto", ttl=300) as lease:
    ...
```

The context manager automatically heart-beats the lease in the background, so long-running jobs don't need to pick a huge TTL.

## Passthrough mode

If `GPU_LOCK_URL` is empty or unset, every client call is a no-op: functions return `None`, context managers yield `None`, the CLI's `run` just execs the command. This lets the same code run on single-GPU dev machines without a gpu-lock service.

## Auth

Set `GPU_LOCK_TOKEN` — the client sends it as `Authorization: Bearer`.

See the [project README](https://github.com/ildar-idrisov/gpu-lock) for the full protocol, server setup, and priority semantics.
