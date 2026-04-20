# gpu-lock

[![CI](https://github.com/ildar-idrisov/gpu-lock/actions/workflows/ci.yml/badge.svg)](https://github.com/ildar-idrisov/gpu-lock/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/gpu-lock-client.svg)](https://pypi.org/project/gpu-lock-client/)
[![License: Apache-2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)

FIFO priority mutex for shared GPU access across Docker containers. Each managed GPU gets its own independent queue. One holder per GPU at a time, auto-release on TTL, renewable via heartbeat, per-GPU priorities, optional bearer-token auth and on-disk persistence.

> **Use at your own risk.** This project manages access coordination but does not itself prevent processes from using a GPU. It's up to callers to respect the lease (e.g. set `CUDA_VISIBLE_DEVICES`). The CLI does this for you automatically; the Python client makes it one line.

## When to use

Multiple GPU-heavy containers share a host and each wants the whole card (LLM servers, image generators, long-running training). Without coordination they OOM each other. gpu-lock gives them one waiting line.

```
Container A ──POST /acquire?gpu=0──────────────┐
                                               │
Container B ──POST /acquire?gpu=1──────────────┤──→  gpu-lock  ──┬─→ FIFO queue for GPU 0
                                               │                 └─→ FIFO queue for GPU 1
Container C ──POST /acquire?gpu=auto&prio=high─┘                     (priority + least-busy)
```

## Repository layout

| Path | Purpose |
|---|---|
| `server/` | FastAPI service. Ships as a Docker image (`ghcr.io/<owner>/gpu-lock/server`). |
| `client/` | Python client + `gpu-lock` CLI. Published to PyPI as `gpu-lock-client`. |
| `tests/` | pytest suite (exercises the server via in-process ASGI transport). |
| `LICENSE` | Apache-2.0. |
| `CHANGELOG.md` | Release notes, Keep a Changelog format. |

## Installing in your project

Install the **client** from PyPI — it includes the `gpu-lock` CLI as well:

```bash
# uv
uv add 'gpu-lock-client>=0.2,<0.3'

# poetry
poetry add 'gpu-lock-client@^0.2.0'

# plain pip
pip install 'gpu-lock-client>=0.2,<0.3'
```

`pyproject.toml`:

```toml
[project]
dependencies = [
  "gpu-lock-client>=0.2,<0.3",
]
```

**Pre-release / unreleased main branch:**

```bash
pip install 'git+https://github.com/ildar-idrisov/gpu-lock@main#subdirectory=client'
```

**Editable local install** (when developing gpu-lock and a consumer side-by-side):

```bash
pip install -e ../gpu-lock/client
```

Don't commit editable references to `pyproject.toml` — keep them on your machine only.

The **server** is not on PyPI. Run it as the published Docker image:

```bash
docker pull ghcr.io/ildar-idrisov/gpu-lock/server:latest
```

## Quick start

**Run the server.**

```bash
# Single GPU
GPU_IDS=0 docker compose -f server/docker-compose.yml up -d

# Two GPUs, auth on, state persisted
GPU_IDS=0,1 \
GPU_LOCK_TOKEN=$(openssl rand -hex 32) \
docker compose -f server/docker-compose.yml up -d

curl http://localhost:8090/health
```

**Use the CLI.**

```bash
pip install gpu-lock-client
export GPU_LOCK_URL=http://localhost:8090
export GPU_LOCK_TOKEN=...   # if the server has auth on

gpu-lock run --gpu 0 -- python train.py
gpu-lock run --gpu auto --priority high -- python infer.py
gpu-lock run --gpu 0  --priority low  -- python nightly.py
gpu-lock status
```

**Use from Python.**

```python
from gpu_lock_client import gpu_lock, gpu_lock_sync, Priority

async with gpu_lock("my-service", gpu="auto", ttl=600, priority=Priority.HIGH) as lease:
    if lease:
        os.environ["CUDA_VISIBLE_DEVICES"] = str(lease.gpu)
    run_the_thing()
```

The context manager heart-beats the lease in the background, so long jobs don't need a huge TTL and short crashes don't strand the GPU.

## API

All endpoints require the `Authorization: Bearer <token>` header when `GPU_LOCK_TOKEN` is set on the server. `/health` is always public.

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/acquire?owner=&gpu=&ttl=&wait_timeout=&priority=` | Enqueue and wait for a lease. Blocks up to `wait_timeout` seconds. |
| `POST` | `/release/{lease_id}` | Release a held lease, or cancel a queued one. |
| `POST` | `/renew/{lease_id}?ttl=` | Extend a held lease. `ttl` optional (defaults to last value). |
| `GET`  | `/status[?gpu=]` | Full state — holder + queue per GPU (or one GPU). |
| `GET`  | `/queue[?gpu=]` | Cheap stats — length and busy flag per GPU (or one GPU). |
| `GET`  | `/health` | Liveness + server info (`{status, gpu_ids, version, shutting_down}`). |

### Parameters

| Param | Type | Default | Notes |
|---|---|---|---|
| `gpu` | `int` or `"auto"` | **required** | Specific GPU id, or `auto` to pick the least-loaded managed GPU. |
| `owner` | str | `"unknown"` | Free-form label shown in `/status`. |
| `ttl` | float (s) | `300` | Lease lives this long after grant. Extend with `/renew`. |
| `wait_timeout` | float (s) | `300` | Max time the client is willing to wait in queue. Server returns 408 when exceeded. |
| `priority` | enum | `normal` | `immediate` → head of queue (does **not** preempt current holder). `high` → before `normal`. `low` → behind everyone. |

### Lease object

```json
{
  "lease_id": "abc123def456",
  "owner": "my-service",
  "gpu": 0,
  "priority": "high",
  "ttl": 300,
  "expires_at": 1745222340.7
}
```

## Server configuration (env vars)

| Var | Default | Notes |
|---|---|---|
| `GPU_IDS` | `0` | Comma-separated list of managed GPU indices. Required at least one. |
| `GPU_LOCK_TOKEN` | _(empty)_ | Bearer token. Empty = auth disabled. |
| `GPU_LOCK_STATE_FILE` | _(empty)_ | JSON snapshot path. Empty = in-memory only (lost on restart). |
| `GPU_LOCK_LOG_FILE` | _(empty)_ | Additional JSON log sink. Stdout is always on. |
| `GPU_LOCK_LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR`. |
| `GPU_LOCK_SHUTDOWN_DRAIN` | `5` | Seconds to wait for in-flight requests to finish on SIGTERM. |
| `GPU_LOCK_HOST` / `GPU_LOCK_PORT` | `0.0.0.0` / `8090` | Bind address. |

## Client configuration (env vars)

| Var | Used by | Notes |
|---|---|---|
| `GPU_LOCK_URL` | client + CLI | If empty, the client becomes a no-op (passthrough). |
| `GPU_LOCK_TOKEN` | client + CLI | Sent as `Authorization: Bearer` on every request. |
| `GPU_LOCK_GPU` | CLI | Default `--gpu` value. |
| `GPU_LOCK_OWNER` | CLI | Default `--owner` value. |
| `GPU_LOCK_TTL` | CLI | Default `--ttl`. |
| `GPU_LOCK_WAIT_TIMEOUT` | CLI | Default `--wait-timeout`. |
| `GPU_LOCK_PRIORITY` | CLI | Default `--priority`. |

## Priority semantics

Four levels, in ascending order of urgency:

- **`low`** — for background batches that should yield to everything else. Starves when any `normal` or higher work is queued.
- **`normal`** — default. FIFO among peers.
- **`high`** — for user-facing / interactive work. Runs before `normal`.
- **`immediate`** — runs as soon as the current holder finishes; jumps to the head of the queue. **Does not preempt** the current holder — that would need a cooperative abort protocol we don't provide.

Within a priority level, ordering is FIFO by enqueue time. Priority is evaluated at grant time from the full queue: adding a new `immediate` request while normal work is queued makes the `immediate` request jump to the front.

## Persistence — why it matters

Without persistence, every server restart wipes the lease table. A client that thinks it holds GPU 0 will keep running, while a fresh server happily grants GPU 0 to someone else — two processes fight for one VRAM.

With `GPU_LOCK_STATE_FILE` set, the server atomically snapshots state to disk on every change and restores it on startup. Wall-clock `expires_at` is persisted, so remaining TTL is preserved across restart.

Graceful shutdown (SIGTERM → drain → exit) preserves holders in the state file and notifies queued waiters with 503. Those clients are expected to retry.

## Development

```bash
# Set up a venv with both packages in editable mode
python -m venv .venv && source .venv/bin/activate
pip install -e ./server -e ./client
pip install pytest pytest-asyncio

# Run tests
pytest tests/ -v

# Run the server locally
GPU_IDS=0,1 python -m gpu_lock_server

# Try the CLI
GPU_LOCK_URL=http://localhost:8090 gpu-lock status
```

Build distributions:

```bash
python -m build client/
python -m build server/
```

## Releasing

See [PUBLISHING.md](PUBLISHING.md) for the full step-by-step procedure (one-time PyPI Trusted Publishing setup + per-release loop). Short version: bump version in `client/pyproject.toml`, update `CHANGELOG.md`, tag `vX.Y.Z`, push tag — CI publishes to PyPI, pushes to GHCR, and creates a GitHub Release automatically.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

Apache-2.0 — see [LICENSE](LICENSE). The project is provided "as is" without warranty; you assume all risk from using it to coordinate GPU access.
