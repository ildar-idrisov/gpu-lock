"""Cyclic JSONL history of GPU-lock state changes.

Why
---
Operators and downstream services need to know "did anyone touch this GPU in
the last N minutes?" without scraping process logs. A revision-style background
task uses this to back off when interactive users have been busy, the way a
polite roommate notices the kettle is warm and doesn't barge in.

What
----
- One JSONL file per rotation period (default 1h). Filename is the UTC
  open-time stamp: ``YYYY-MM-DD_HH-MM-SS.jsonl`` so listing-by-name is
  ordered-by-time.
- Each event is one JSON line: ``ts``, ``event``, ``lease_id``, ``owner``,
  ``gpu``, ``priority``, plus lease-shape fields for context.
- On rotation we delete files older than the retention window
  (default 24h), so disk usage stays bounded without a separate cron job.

The writer is sync (single ``open(..).write()`` per event) — events are tiny
and rare compared to GPU work itself, so the cost is negligible and we avoid
the complexity of an async queue.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

log = logging.getLogger(__name__)

_FILENAME_FMT = "%Y-%m-%d_%H-%M-%S"


class HistoryWriter:
    """Append-only JSONL writer with periodic rotation + retention sweep."""

    def __init__(
        self,
        directory: str | os.PathLike[str] | None,
        rotate_sec: int = 3600,
        retention_sec: int = 86400,
    ) -> None:
        self.directory = Path(directory) if directory else None
        self.rotate_sec = max(60, int(rotate_sec))
        self.retention_sec = max(self.rotate_sec, int(retention_sec))
        self._lock = threading.Lock()
        self._current_path: Path | None = None
        self._current_open_ts: float | None = None
        if self.directory is not None:
            self.directory.mkdir(parents=True, exist_ok=True)

    @property
    def enabled(self) -> bool:
        return self.directory is not None

    def write(self, event: str, lease: Any | None = None, **extra: Any) -> None:
        if not self.enabled:
            return
        record: dict[str, Any] = {
            "ts": time.time(),
            "event": event,
        }
        if lease is not None:
            record.update(_lease_fields(lease))
        if extra:
            record.update(extra)
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
        with self._lock:
            try:
                path = self._ensure_current_locked()
                with path.open("a", encoding="utf-8") as f:
                    f.write(line + "\n")
            except OSError as exc:
                log.error(
                    "history write failed",
                    extra={"event": "history_write_error",
                           "error": str(exc), "path": str(self._current_path)},
                )

    def close(self) -> None:
        # Files are opened/closed per write. Nothing to do, kept for symmetry.
        pass

    # -- queries -------------------------------------------------------------

    def query(
        self,
        since_ts: float,
        until_ts: float | None = None,
        gpu: int | None = None,
        exclude_owners: Iterable[str] | None = None,
        include_owners: Iterable[str] | None = None,
        events: Iterable[str] | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        if not self.enabled or self.directory is None:
            return []
        until_ts = until_ts if until_ts is not None else time.time()
        excl = set(exclude_owners) if exclude_owners else None
        incl = set(include_owners) if include_owners else None
        evt_filter = set(events) if events else None
        results: list[dict[str, Any]] = []
        for path in self._files_overlapping(since_ts, until_ts):
            try:
                with path.open("r", encoding="utf-8") as f:
                    for raw in f:
                        raw = raw.strip()
                        if not raw:
                            continue
                        try:
                            rec = json.loads(raw)
                        except json.JSONDecodeError:
                            continue
                        ts = rec.get("ts")
                        if not isinstance(ts, (int, float)):
                            continue
                        if ts < since_ts or ts > until_ts:
                            continue
                        if gpu is not None and rec.get("gpu") != gpu:
                            continue
                        owner = rec.get("owner")
                        if excl and owner in excl:
                            continue
                        if incl and owner not in incl:
                            continue
                        if evt_filter and rec.get("event") not in evt_filter:
                            continue
                        results.append(rec)
            except OSError as exc:
                log.warning("history read failed",
                            extra={"event": "history_read_error",
                                   "path": str(path), "error": str(exc)})
        results.sort(key=lambda r: r.get("ts", 0))
        if limit is not None and len(results) > limit:
            results = results[-limit:]
        return results

    # -- internals -----------------------------------------------------------

    def _ensure_current_locked(self) -> Path:
        assert self.directory is not None
        now = time.time()
        if (
            self._current_path is None
            or self._current_open_ts is None
            or now - self._current_open_ts >= self.rotate_sec
        ):
            stamp = datetime.fromtimestamp(now, tz=timezone.utc).strftime(_FILENAME_FMT)
            self._current_path = self.directory / f"{stamp}.jsonl"
            self._current_open_ts = now
            self._sweep_old_locked(now)
        return self._current_path

    def _sweep_old_locked(self, now: float) -> None:
        assert self.directory is not None
        cutoff = now - self.retention_sec
        for path in self.directory.glob("*.jsonl"):
            ts = _filename_ts(path.name)
            if ts is None:
                continue
            # Keep a file if any of its potential events could still be inside
            # the retention window. With rotate_sec rotation the file covers
            # [ts, ts+rotate_sec], so drop only when even the latest possible
            # event would be older than the cutoff.
            if ts + self.rotate_sec < cutoff:
                try:
                    path.unlink()
                except OSError as exc:
                    log.warning("history sweep failed",
                                extra={"event": "history_sweep_error",
                                       "path": str(path), "error": str(exc)})

    def _files_overlapping(self, since_ts: float, until_ts: float) -> list[Path]:
        assert self.directory is not None
        out: list[Path] = []
        for path in self.directory.glob("*.jsonl"):
            ts = _filename_ts(path.name)
            if ts is None:
                continue
            file_end = ts + self.rotate_sec
            if file_end < since_ts or ts > until_ts:
                continue
            out.append(path)
        out.sort(key=lambda p: p.name)
        return out


def _lease_fields(lease: Any) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    for attr in ("lease_id", "owner", "gpu", "ttl", "wait_timeout",
                 "enqueued_at", "granted_at", "expires_at"):
        val = getattr(lease, attr, None)
        if val is not None:
            fields[attr] = val
    prio = getattr(lease, "priority", None)
    if prio is not None:
        name = getattr(prio, "name", None)
        fields["priority"] = name.lower() if name else str(prio)
    return fields


def _filename_ts(name: str) -> float | None:
    if not name.endswith(".jsonl"):
        return None
    stem = name[: -len(".jsonl")]
    try:
        dt = datetime.strptime(stem, _FILENAME_FMT).replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return dt.timestamp()
