# Contributing

## Development loop

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ./server -e ./client
pip install pytest pytest-asyncio ruff mypy
pytest tests/ -v
```

## Code style

- `ruff check server client tests` — no lint errors before PR.
- Keep public APIs backwards-compatible within a minor version. Breaking changes land on the next major and get documented in `CHANGELOG.md`.
- Server modules stay under ~300 lines each. Prefer splitting over growing `manager.py`.

## Commit messages

Short imperative summary; body optional. No strict convention, but tagging the area helps (`server:`, `client:`, `cli:`, `ci:`).

## Adding a server endpoint

1. Route in `server/gpu_lock_server/app.py`.
2. Business logic in `server/gpu_lock_server/manager.py` (keep state mutations behind the queue's `_lock`).
3. Update `client/gpu_lock_client/_client.py` with the corresponding HTTP call.
4. Add a test in `tests/`.
5. Document in `README.md` and `CHANGELOG.md`.

## Tests

- Each test builds its own app via the `make_app` fixture so settings are isolated.
- Prefer `httpx.ASGITransport` over spinning up uvicorn — fast and deterministic.
- Don't sleep longer than a few hundred ms in tests.

## Releasing

Maintainers only. See [PUBLISHING.md](PUBLISHING.md).

## Reporting security issues

Please email the maintainer instead of filing a public issue for anything auth-related or that could be used to bypass lease isolation.
