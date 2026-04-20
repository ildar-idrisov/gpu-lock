"""Shared fixtures.

All tests spawn an in-process FastAPI app with httpx.ASGITransport — no
network, no uvicorn, deterministic. Each test builds its own Settings so we
can configure auth, persistence, and gpu_ids per test.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "server"))
sys.path.insert(0, str(_REPO / "client"))


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Wipe all GPU_LOCK_* env before each test so modules don't see stale state."""
    for key in list(os.environ):
        if key.startswith("GPU_LOCK_") or key == "GPU_IDS":
            monkeypatch.delenv(key, raising=False)
    yield


@pytest.fixture
def make_app():
    """Build an app with per-test settings."""
    from gpu_lock_server.app import create_app
    from gpu_lock_server.config import Settings

    def _factory(
        gpu_ids=(0,),
        auth_token=None,
        state_file=None,
        log_level="WARNING",
        shutdown_drain=2.0,
    ):
        settings = Settings(
            gpu_ids=list(gpu_ids),
            auth_token=auth_token,
            state_file=str(state_file) if state_file else None,
            log_file=None,
            log_level=log_level,
            shutdown_drain_seconds=shutdown_drain,
        )
        return create_app(settings)

    return _factory


@pytest.fixture
async def client(make_app):
    """Async HTTP client pointed at a fresh app with GPUs [0, 1]."""
    import httpx

    app = make_app(gpu_ids=(0, 1))
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            yield c
