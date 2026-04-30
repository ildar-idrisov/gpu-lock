"""Tests for the client-side history + is_idle helpers.

We can't easily point the client at the in-process FastAPI app (the client
uses a fresh httpx connection, the in-process tests use ASGITransport). For
HTTP-level coverage the server-side ``/history`` endpoint is exercised in
``test_history.py``. Here we focus on the client's branching: passthrough
mode, parameter validation, and the queue-shape parsing in ``is_idle``.
"""
from __future__ import annotations

from typing import Any, Optional
from unittest.mock import AsyncMock, patch

import pytest

from gpu_lock_client._client import _queue_is_empty


def test_is_idle_passthrough_returns_true(monkeypatch: pytest.MonkeyPatch) -> None:
    """No GPU_LOCK_URL → assume single-tenant, allow background work."""
    from gpu_lock_client import is_idle_sync
    monkeypatch.delenv("GPU_LOCK_URL", raising=False)
    assert is_idle_sync(gpu=0) is True


def test_history_passthrough_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    from gpu_lock_client import history_sync
    monkeypatch.delenv("GPU_LOCK_URL", raising=False)
    assert history_sync(since_minutes=10) is None


def test_history_requires_window(monkeypatch: pytest.MonkeyPatch) -> None:
    from gpu_lock_client import history_sync
    monkeypatch.setenv("GPU_LOCK_URL", "http://nonexistent:9999")
    with pytest.raises(ValueError):
        history_sync()


def test_queue_is_empty_single_gpu_form() -> None:
    assert _queue_is_empty({"gpu": 0, "queue_length": 0, "busy": False}, gpu=0)
    assert not _queue_is_empty({"gpu": 0, "queue_length": 1, "busy": False}, gpu=0)
    assert not _queue_is_empty({"gpu": 0, "queue_length": 0, "busy": True}, gpu=0)


def test_queue_is_empty_multi_gpu_form() -> None:
    payload = {
        "gpus": {
            "0": {"gpu": 0, "queue_length": 0, "busy": False},
            "1": {"gpu": 1, "queue_length": 2, "busy": True},
        },
        "gpu_ids": [0, 1],
    }
    assert _queue_is_empty(payload, gpu=0)
    assert not _queue_is_empty(payload, gpu=1)
    assert not _queue_is_empty(payload, gpu=None)  # any gpu busy → not idle


@pytest.mark.asyncio
async def test_is_idle_async_combines_queue_and_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When queue is empty and history shows zero recent events, idle = True."""
    from gpu_lock_client import _client

    monkeypatch.setenv("GPU_LOCK_URL", "http://stub")

    async def fake_queue(gpu: Optional[int] = None) -> dict[str, Any]:
        return {"gpu": 0, "queue_length": 0, "busy": False}

    async def fake_history(**_: Any) -> dict[str, Any]:
        return {"since_ts": 0, "until_ts": 0, "count": 0, "events": []}

    with patch.object(_client, "queue_info_async", AsyncMock(side_effect=fake_queue)), \
         patch.object(_client, "history_async", AsyncMock(side_effect=fake_history)):
        assert await _client.is_idle_async(gpu=0) is True


@pytest.mark.asyncio
async def test_is_idle_async_blocks_on_recent_activity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gpu_lock_client import _client

    monkeypatch.setenv("GPU_LOCK_URL", "http://stub")

    async def fake_queue(gpu: Optional[int] = None) -> dict[str, Any]:
        return {"gpu": 0, "queue_length": 0, "busy": False}

    async def fake_history(**_: Any) -> dict[str, Any]:
        return {
            "since_ts": 0,
            "until_ts": 0,
            "count": 1,
            "events": [{"ts": 1, "event": "grant", "owner": "user"}],
        }

    with patch.object(_client, "queue_info_async", AsyncMock(side_effect=fake_queue)), \
         patch.object(_client, "history_async", AsyncMock(side_effect=fake_history)):
        assert await _client.is_idle_async(gpu=0) is False


@pytest.mark.asyncio
async def test_is_idle_async_blocks_on_busy_queue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gpu_lock_client import _client

    monkeypatch.setenv("GPU_LOCK_URL", "http://stub")

    async def fake_queue(gpu: Optional[int] = None) -> dict[str, Any]:
        return {"gpu": 0, "queue_length": 0, "busy": True}

    with patch.object(_client, "queue_info_async", AsyncMock(side_effect=fake_queue)):
        assert await _client.is_idle_async(gpu=0) is False
