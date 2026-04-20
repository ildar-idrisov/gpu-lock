"""Multi-GPU isolation: independent queues, auto selection."""
from __future__ import annotations

import asyncio

import pytest

pytestmark = pytest.mark.asyncio


async def test_two_gpus_parallel_no_blocking(client):
    """Holding GPU 0 should not delay a request for GPU 1."""
    a = (await client.post("/acquire", params={"owner": "A", "gpu": 0})).json()
    b = (await client.post("/acquire", params={"owner": "B", "gpu": 1})).json()

    assert a["gpu"] == 0
    assert b["gpu"] == 1

    s = (await client.get("/status")).json()
    assert s["gpus"]["0"]["holder"]["owner"] == "A"
    assert s["gpus"]["1"]["holder"]["owner"] == "B"


async def test_auto_picks_less_loaded(client):
    await client.post("/acquire", params={"owner": "X", "gpu": 0})
    # GPU 1 is idle — auto must pick 1.
    r = await client.post("/acquire", params={"owner": "Y", "gpu": "auto"})
    assert r.json()["gpu"] == 1


async def test_auto_with_equal_load_picks_lowest_id(client):
    # Both idle; auto should prefer 0.
    r = await client.post("/acquire", params={"owner": "X", "gpu": "auto"})
    assert r.json()["gpu"] == 0


async def test_release_finds_correct_gpu(client):
    """Release by lease_id works regardless of which GPU holds it."""
    b = (await client.post("/acquire", params={"owner": "B", "gpu": 1})).json()
    r = await client.post(f"/release/{b['lease_id']}")
    assert r.status_code == 200
    assert (await client.get("/status", params={"gpu": 1})).json()["holder"] is None
