"""Tests for the cyclic history log.

Covers writer rotation, retention sweep, query filtering, and the
/history HTTP endpoint.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

from gpu_lock_server.history import HistoryWriter
from gpu_lock_server.models import Lease, Priority


def _make_lease(owner: str = "tester", gpu: int = 0, lease_id: str = "abcdef") -> Lease:
    return Lease(
        lease_id=lease_id,
        owner=owner,
        gpu=gpu,
        priority=Priority.NORMAL,
        ttl=300.0,
        wait_timeout=60.0,
    )


def test_writer_creates_file_and_appends(tmp_path: Path) -> None:
    w = HistoryWriter(tmp_path, rotate_sec=3600, retention_sec=86400)
    w.write("grant", _make_lease())
    w.write("release", _make_lease())
    files = list(tmp_path.glob("*.jsonl"))
    assert len(files) == 1
    lines = files[0].read_text().splitlines()
    assert len(lines) == 2
    rec = json.loads(lines[0])
    assert rec["event"] == "grant"
    assert rec["lease_id"] == "abcdef"
    assert rec["owner"] == "tester"
    assert rec["priority"] == "normal"


def test_disabled_when_no_dir() -> None:
    w = HistoryWriter(None)
    assert not w.enabled
    w.write("grant", _make_lease())
    assert w.query(since_ts=0) == []


def test_query_window_filters_by_time(tmp_path: Path) -> None:
    w = HistoryWriter(tmp_path)
    w.write("grant", _make_lease())
    now = time.time()
    results = w.query(since_ts=now - 60)
    assert len(results) == 1
    # Older window (everything before "now" minus 1 day) should be empty.
    results = w.query(since_ts=0, until_ts=now - 3600)
    assert results == []


def test_query_excludes_owners(tmp_path: Path) -> None:
    w = HistoryWriter(tmp_path)
    w.write("grant", _make_lease(owner="user", lease_id="111"))
    w.write("grant", _make_lease(owner="bg-task", lease_id="222"))
    now = time.time()
    out = w.query(since_ts=now - 60, exclude_owners=["bg-task"])
    assert len(out) == 1
    assert out[0]["owner"] == "user"


def test_query_includes_owners(tmp_path: Path) -> None:
    w = HistoryWriter(tmp_path)
    w.write("grant", _make_lease(owner="user", lease_id="111"))
    w.write("grant", _make_lease(owner="bg-task", lease_id="222"))
    now = time.time()
    out = w.query(since_ts=now - 60, include_owners=["bg-task"])
    assert len(out) == 1
    assert out[0]["owner"] == "bg-task"


def test_query_filter_events_and_gpu(tmp_path: Path) -> None:
    w = HistoryWriter(tmp_path)
    w.write("grant", _make_lease(gpu=0, lease_id="a1"))
    w.write("release", _make_lease(gpu=0, lease_id="a1"))
    w.write("grant", _make_lease(gpu=1, lease_id="b1"))
    now = time.time()
    only_gpu1 = w.query(since_ts=now - 60, gpu=1)
    assert all(r["gpu"] == 1 for r in only_gpu1)
    only_grants = w.query(since_ts=now - 60, events=["grant"])
    assert all(r["event"] == "grant" for r in only_grants)


def test_retention_sweeps_old_files(tmp_path: Path) -> None:
    # Manually plant a file older than retention. Filename is the open-time
    # stamp; sweep checks ts + rotate_sec < now - retention_sec.
    old_dt = datetime.now(timezone.utc) - timedelta(days=3)
    old_name = old_dt.strftime("%Y-%m-%d_%H-%M-%S") + ".jsonl"
    old_path = tmp_path / old_name
    old_path.write_text('{"ts": 1, "event": "grant"}\n')
    assert old_path.exists()

    w = HistoryWriter(tmp_path, rotate_sec=3600, retention_sec=86400)
    # Triggering a write forces _ensure_current → sweep.
    w.write("grant", _make_lease())
    assert not old_path.exists()
    # The new file lives.
    fresh = [p for p in tmp_path.glob("*.jsonl") if p.name != old_name]
    assert len(fresh) == 1


@pytest.mark.asyncio
async def test_endpoint_returns_events(make_app, tmp_path: Path) -> None:
    app = make_app(history_dir=tmp_path)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.post(
                "/acquire",
                params={"owner": "tester", "gpu": "0", "ttl": 60, "wait_timeout": 5},
            )
            assert r.status_code == 200
            lease_id = r.json()["lease_id"]
            r = await c.post(f"/release/{lease_id}")
            assert r.status_code == 200

            r = await c.get("/history", params={"since_minutes": 1})
            assert r.status_code == 200
            body = r.json()
            assert body["count"] >= 2
            events = [e["event"] for e in body["events"]]
            assert "grant" in events
            assert "release" in events


@pytest.mark.asyncio
async def test_endpoint_404_when_disabled(make_app) -> None:
    app = make_app()  # no history_dir
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.get("/history", params={"since_minutes": 1})
            assert r.status_code == 404


@pytest.mark.asyncio
async def test_endpoint_400_when_window_missing(make_app, tmp_path: Path) -> None:
    app = make_app(history_dir=tmp_path)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.get("/history")
            assert r.status_code == 400
