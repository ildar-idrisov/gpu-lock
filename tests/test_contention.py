"""Contention: multiple acquires on the same GPU, wait_timeout, cancellation."""
from __future__ import annotations

import asyncio

import pytest

pytestmark = pytest.mark.asyncio


async def test_second_request_waits_then_resumes(client):
    a = (await client.post("/acquire", params={"owner": "A", "gpu": 0, "ttl": 600})).json()

    task = asyncio.create_task(
        client.post("/acquire", params={"owner": "B", "gpu": 0, "wait_timeout": 10})
    )
    await asyncio.sleep(0.1)
    assert not task.done(), "B must be waiting"

    await client.post(f"/release/{a['lease_id']}")
    r = await asyncio.wait_for(task, timeout=3)
    assert r.status_code == 200
    assert r.json()["owner"] == "B"
    assert r.json()["gpu"] == 0


async def test_wait_timeout_returns_408(client):
    await client.post("/acquire", params={"owner": "A", "gpu": 0, "ttl": 600})
    r = await client.post("/acquire", params={"owner": "B", "gpu": 0, "wait_timeout": 1})
    assert r.status_code == 408
