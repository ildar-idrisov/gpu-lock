"""Basic smoke tests: acquire, release, status, queue, health."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio


async def test_health(client):
    r = await client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["gpu_ids"] == [0, 1]


async def test_acquire_release_gpu0(client):
    r = await client.post("/acquire", params={"owner": "a", "gpu": 0})
    assert r.status_code == 200, r.text
    lease = r.json()
    assert lease["gpu"] == 0
    assert lease["owner"] == "a"
    assert lease["priority"] == "normal"

    r = await client.post(f"/release/{lease['lease_id']}")
    assert r.status_code == 200

    r = await client.get("/status", params={"gpu": 0})
    assert r.json()["holder"] is None


async def test_status_and_queue_aggregates(client):
    r = await client.get("/status")
    body = r.json()
    assert set(body["gpus"].keys()) == {"0", "1"}
    assert body["gpu_ids"] == [0, 1]

    r = await client.get("/queue")
    body = r.json()
    assert set(body["gpus"].keys()) == {"0", "1"}


async def test_unknown_gpu_404(client):
    r = await client.post("/acquire", params={"owner": "a", "gpu": 9})
    assert r.status_code == 404


async def test_bad_gpu_param_400(client):
    r = await client.post("/acquire", params={"owner": "a", "gpu": "wtf"})
    assert r.status_code == 400


async def test_gpu_param_required(client):
    r = await client.post("/acquire", params={"owner": "a"})
    assert r.status_code == 422  # FastAPI validation error
