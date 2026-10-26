"""Append-only writer for a run's event log.

FR-EVT-01: the CLI emits one JSON object per line to
``<root>/runs/<run_id>/events.jsonl``. Every event is validated against
``run-event.schema.json`` *before* it is written, and each line is flushed
and ``fsync``-ed so a crash mid-run still leaves a readable, valid prefix.

The emitter owns two things the caller should not set by hand:

* ``seq`` — assigned in order, strictly increasing from 0. A rejected event
  does not consume a number.
* ``ts``  — an RFC 3339 UTC instant stamped at emit time.

Reopening an existing run resumes after the highest ``seq`` on disk, so a
resumed run keeps a single, strictly increasing log.
"""

from __future__ import annotations

import json
import os
import secrets
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from wattnot import schemas
from wattnot.events import schema

__all__ = ["Emitter", "new_run_id"]

_Clock = Callable[[], datetime]


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _iso(dt: datetime) -> str:
    """Format a datetime as an RFC 3339 UTC instant ending in ``Z``."""

    dt = dt.astimezone(UTC)
    if dt.microsecond:
        return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def new_run_id(*, now: _Clock | None = None) -> str:
    """Return a fresh ``run_<YYYYMMDD>_<HHMMSS>_<6 hex>`` id.

    The six-hex suffix keeps ids unique even when two runs start in the same
    second. Matches ``run-event.schema.json``'s ``run_id`` pattern.
    """

    stamp = (now or _utc_now)().astimezone(UTC)
    return f"run_{stamp:%Y%m%d}_{stamp:%H%M%S}_{secrets.token_hex(3)}"


class Emitter:
    """Write validated run events to ``<root>/runs/<run_id>/events.jsonl``.

    Usable as a context manager; ``close()`` releases the file handle. The
    file and its parent directory are created lazily on the first event that
    passes validation, so a run that emits nothing leaves no file behind.
    """

    def __init__(
        self,
        run_id: str,
        *,
        root: str | os.PathLike[str] = ".",
        now: _Clock | None = None,
    ) -> None:
        if not schema.RUN_ID_RE.match(run_id):
            raise ValueError(f"run_id {run_id!r} does not match the schema pattern")
        self.run_id = run_id
        self.path = Path(root) / "runs" / run_id / "events.jsonl"
        self._now = now or _utc_now
        self._next_seq = self._resume_seq()
        self._handle = None
        self._closed = False

    def _resume_seq(self) -> int:
        """Pick up after the highest ``seq`` already on disk (0 for a new run)."""

        if not self.path.exists():
            return 0
        events = schemas.load_run_log(self.path)
        if not events:
            return 0
        return max(e["seq"] for e in events) + 1

    def emit(
        self,
        type: str,
        data: Any,
        *,
        step_id: str | None = None,
        parent_step_id: str | None = None,
    ) -> dict:
        """Validate, write, flush and fsync one event; return it.

        Raises :class:`ValidationError` if the event fails the schema. In that
        case nothing is written and the next ``seq`` is left untouched.
        """

        if self._closed:
            raise ValueError("cannot emit on a closed emitter")

        event = {
            "schema_version": schema.SCHEMA_VERSION,
            "run_id": self.run_id,
            "seq": self._next_seq,
            "ts": _iso(self._now()),
            "type": type,
            "step_id": step_id,
            "parent_step_id": parent_step_id,
            "data": data,
        }
        # Validate before the write so a bad event never reaches the log.
        schema.check_event(event)

        self._write(event)
        self._next_seq += 1
        return event

    def _write(self, event: dict) -> None:
        if self._handle is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._handle = self.path.open("a", encoding="utf-8")
        line = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
        self._handle.write(line + "\n")
        self._handle.flush()
        os.fsync(self._handle.fileno())

    def close(self) -> None:
        """Release the file handle. Safe to call more than once.

        Every event is already flushed and fsync-ed as it is written, so there
        is nothing left to force here.
        """

        if self._handle is not None:
            self._handle.close()
            self._handle = None
        self._closed = True

    def __enter__(self) -> Emitter:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
