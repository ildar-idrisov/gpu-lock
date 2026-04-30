# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.3.0] - 2026-04-30

### Added

- Cyclic JSONL history of acquire/release events. The server writes one event
  per state change (`enqueue`, `grant`, `release`, `cancel`, `renew`,
  `ttl_expire`, `wait_timeout`) into a hourly-rotated file under
  `GPU_LOCK_HISTORY_DIR`. Old files are swept on rotation by
  `GPU_LOCK_HISTORY_RETENTION_SEC` (default 24h).
- `GET /history?since_minutes=...&exclude_owners=...&gpu=...` for querying
  recent events. Supports `since_seconds`, `since_minutes`, `since_hours`,
  `since_days`, `gpu`, `exclude_owners`, `include_owners`, `events`, `limit`.
- Client functions `history_async` / `history_sync` and the convenience
  helper `is_idle_async` / `is_idle_sync` — "true iff queue empty AND no
  acquires in the last N minutes by anyone other than `exclude_owners`."
  Designed for off-schedule background revisions that should back off when
  interactive users are active.

### Changed

- `/health` now reports `history_enabled`.

## [0.2.1] - 2026-04-20

### Fixed

- Pinned the server `Dockerfile` to a real `python:3.12-slim` digest (the
  previous pin pointed to a non-existent digest, breaking the GHCR image
  build in the v0.2.0 release pipeline).
- Re-publish of `gpu-lock-client` because v0.2.0 PyPI artifacts were uploaded
  from a partially-fixed pipeline; v0.2.1 ships the same code from a clean
  release. No API or behavior changes vs. v0.2.0.

## [0.2.0] - 2026-04-20

### Added

- Multi-GPU support. Server manages one FIFO queue per GPU index configured
  via `GPU_IDS`. `POST /acquire` takes a required `gpu` parameter (int id or
  `auto` for least-busy selection).
- Priority levels: `immediate`, `high`, `normal` (default), `low`. Queue order
  is `(priority desc, enqueued_at asc)`. `immediate` does not preempt the
  current holder — it jumps to the head of the queue and runs next.
- Lease lifecycle split into `lease_ttl` (auto-release timer, renewable via
  `POST /renew/<lease_id>`) and `wait_timeout` (how long a client is willing
  to wait in queue).
- Optional bearer-token authentication via `GPU_LOCK_TOKEN` env var. Enforced
  on every endpoint except `/health`.
- Optional on-disk persistence via `GPU_LOCK_STATE_FILE`. Atomic snapshot on
  every state change; restored on startup with remaining TTLs intact.
- Graceful shutdown: on SIGTERM the server rejects new `/acquire` with 503,
  unblocks waiters, releases active leases, and flushes the state file.
- Structured JSON logging. Optional file sink via `GPU_LOCK_LOG_FILE`.
- Click-based CLI (`gpu-lock`) packaged with the client, replacing the old
  `gpu-run` bash wrapper. Sets `CUDA_VISIBLE_DEVICES` automatically after
  acquiring a lease.
- Monorepo split into `server/` and `client/` packages. Only the client is
  published to PyPI (`gpu-lock-client`); the server ships as a Docker image.

### Changed

- Python client yields `Lease` dataclass (`lease_id`, `owner`, `gpu`,
  `priority`, `expires_at`) instead of a bare lease-id string.
- `/status` and `/queue` return per-GPU data under a `gpus` mapping.

## [0.1.0] - 2026-04-13

### Added

- Initial single-GPU FIFO mutex with HTTP API.

[Unreleased]: https://github.com/ildar-idrisov/gpu-lock/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/ildar-idrisov/gpu-lock/compare/v0.2.1...v0.3.0
[0.2.1]: https://github.com/ildar-idrisov/gpu-lock/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/ildar-idrisov/gpu-lock/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/ildar-idrisov/gpu-lock/releases/tag/v0.1.0
