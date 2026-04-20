"""Bearer-token authentication middleware."""
from __future__ import annotations

import httpx
import pytest

pytestmark = pytest.mark.asyncio


async def _build_client(make_app, token=None):
    app = make_app(gpu_ids=(0,), auth_token=token)
    return app, httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    )


async def test_no_token_required_when_disabled(make_app):
    app, c = await _build_client(make_app, token=None)
    async with app.router.lifespan_context(app), c:
        r = await c.post("/acquire", params={"owner": "a", "gpu": 0})
        assert r.status_code == 200


async def test_missing_auth_returns_401(make_app):
    app, c = await _build_client(make_app, token="s3cr3t")
    async with app.router.lifespan_context(app), c:
        r = await c.post("/acquire", params={"owner": "a", "gpu": 0})
        assert r.status_code == 401


async def test_wrong_auth_returns_403(make_app):
    app, c = await _build_client(make_app, token="s3cr3t")
    async with app.router.lifespan_context(app), c:
        r = await c.post(
            "/acquire",
            params={"owner": "a", "gpu": 0},
            headers={"Authorization": "Bearer wrong"},
        )
        assert r.status_code == 403


async def test_bearer_token_accepted(make_app):
    app, c = await _build_client(make_app, token="s3cr3t")
    async with app.router.lifespan_context(app), c:
        r = await c.post(
            "/acquire",
            params={"owner": "a", "gpu": 0},
            headers={"Authorization": "Bearer s3cr3t"},
        )
        assert r.status_code == 200


async def test_x_api_key_accepted(make_app):
    app, c = await _build_client(make_app, token="s3cr3t")
    async with app.router.lifespan_context(app), c:
        r = await c.post(
            "/acquire",
            params={"owner": "a", "gpu": 0},
            headers={"X-Api-Key": "s3cr3t"},
        )
        assert r.status_code == 200


async def test_health_always_public(make_app):
    app, c = await _build_client(make_app, token="s3cr3t")
    async with app.router.lifespan_context(app), c:
        r = await c.get("/health")
        assert r.status_code == 200
