"""State file persistence across simulated restart."""
from __future__ import annotations

import httpx
import pytest

pytestmark = pytest.mark.asyncio


async def _run_app(app, coro):
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as c:
            return await coro(c)


async def test_state_survives_restart(make_app, tmp_path):
    state = tmp_path / "state.json"

    app1 = make_app(gpu_ids=(0, 1), state_file=state)

    async def first(c):
        r = await c.post("/acquire", params={"owner": "A", "gpu": 0, "ttl": 600})
        return r.json()

    lease = await _run_app(app1, first)
    assert lease["gpu"] == 0

    # New app instance → simulates a restart with the same state file.
    app2 = make_app(gpu_ids=(0, 1), state_file=state)

    async def second(c):
        s = (await c.get("/status", params={"gpu": 0})).json()
        return s

    status = await _run_app(app2, second)
    assert status["holder"] is not None, "holder should be restored from state file"
    assert status["holder"]["lease_id"] == lease["lease_id"]
    assert status["holder"]["owner"] == "A"
