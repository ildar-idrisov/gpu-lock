"""Priority queue ordering.

`immediate` jumps to the head (but does not preempt the current holder);
`high` ahead of `normal`; `low` runs last.
"""
from __future__ import annotations

import asyncio

import pytest

pytestmark = pytest.mark.asyncio


async def _acquire_bg(client, owner, **params):
    """Start an acquire request in the background. Returns the task."""
    return asyncio.create_task(
        client.post("/acquire", params={"owner": owner, "gpu": 0, **params})
    )


async def _queue_len(client, gpu=0):
    s = (await client.get("/status", params={"gpu": gpu})).json()
    return s["queue_length"]


async def test_priority_ordering(client):
    """holder=A, then queue up low/normal/high/immediate; on release they fire in
    (immediate, high, normal, low) order."""
    a = (await client.post("/acquire", params={"owner": "A", "gpu": 0, "ttl": 600})).json()

    low = await _acquire_bg(client, "low", priority="low", wait_timeout=30)
    normal = await _acquire_bg(client, "normal", priority="normal", wait_timeout=30)
    high = await _acquire_bg(client, "high", priority="high", wait_timeout=30)
    immediate = await _acquire_bg(client, "imm", priority="immediate", wait_timeout=30)

    # Give the server a moment to enqueue all four.
    for _ in range(40):
        if await _queue_len(client) == 4:
            break
        await asyncio.sleep(0.02)
    assert await _queue_len(client) == 4

    # Release A → immediate should win.
    await client.post(f"/release/{a['lease_id']}")
    first = await asyncio.wait_for(immediate, timeout=5)
    assert first.status_code == 200
    assert first.json()["owner"] == "imm"

    # Release immediate → high wins.
    await client.post(f"/release/{first.json()['lease_id']}")
    second = await asyncio.wait_for(high, timeout=5)
    assert second.json()["owner"] == "high"

    # Release high → normal wins.
    await client.post(f"/release/{second.json()['lease_id']}")
    third = await asyncio.wait_for(normal, timeout=5)
    assert third.json()["owner"] == "normal"

    # Release normal → low last.
    await client.post(f"/release/{third.json()['lease_id']}")
    fourth = await asyncio.wait_for(low, timeout=5)
    assert fourth.json()["owner"] == "low"


async def test_immediate_does_not_preempt(client):
    """An `immediate` request must wait for the current holder to release."""
    a = (await client.post("/acquire", params={"owner": "A", "gpu": 0, "ttl": 600})).json()

    imm_task = await _acquire_bg(client, "imm", priority="immediate", wait_timeout=10)
    # Give it time to register.
    for _ in range(20):
        if await _queue_len(client) == 1:
            break
        await asyncio.sleep(0.05)
    assert await _queue_len(client) == 1

    # Holder is still A.
    s = (await client.get("/status", params={"gpu": 0})).json()
    assert s["holder"]["owner"] == "A"

    await client.post(f"/release/{a['lease_id']}")
    done = await asyncio.wait_for(imm_task, timeout=3)
    assert done.json()["owner"] == "imm"


async def test_default_priority_is_normal(client):
    a = (await client.post("/acquire", params={"owner": "A", "gpu": 0})).json()
    assert a["priority"] == "normal"


async def test_bad_priority_400(client):
    r = await client.post("/acquire", params={"owner": "A", "gpu": 0, "priority": "supersonic"})
    assert r.status_code == 400
