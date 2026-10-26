"""The FastAPI read-only API over a run's event log (FR-EVT-02 … FR-EVT-05).

Everything is read from ``<root>/runs/<run_id>/events.jsonl`` (and, for signed
runs, ``<root>/receipts/<receipt_id>.json``). The disk log is the truth; the
JSON endpoints and the SSE stream are two views of the same bytes, so a
frontend renders a run identically whether it polls ``GET /api/runs/{id}`` or
reads the ``/events`` stream (FR-EVT-03 replay).

Endpoints:

* ``GET /api/runs``                   — one summary per run on disk.
* ``GET /api/runs/{run_id}``          — ``{run_id, summary, events[], receipt}``.
* ``GET /api/runs/{run_id}/events``   — SSE replay of the log; honours the
  ``Last-Event-ID`` header (resume after that ``seq``) and ``?speed=`` pacing
  (default ``1``; ``0`` = as fast as possible). Finished runs close after
  ``run.finished``.
* ``GET /api/receipts/{receipt_id}``  — a signed receipt by id.
* ``GET /api/pareto/{run_id}``        — the latest Pareto front for a run.

No endpoint mutates anything or starts work — runs begin from the CLI only.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from collections.abc import AsyncIterator
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from wattnot import schemas
from wattnot.events import schema as event_schema

__all__ = ["app", "create_app"]

# The canonical run-id pattern, borrowed from the frozen event schema so the
# API and the emitter agree on what a valid id looks like.
_RUN_ID_RE = event_schema.RUN_ID_RE
# A receipt id becomes a file name, so keep it to safe characters: no slashes
# or dots that could walk out of the receipts directory.
_RECEIPT_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _dumps(obj: Any) -> str:
    """Serialise exactly as the emitter writes the log, so SSE ``data`` frames
    are byte-for-byte the events the JSON endpoints return."""

    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def _runs_dir(root: Path) -> Path:
    return root / "runs"


def _run_log_path(root: Path, run_id: str) -> Path:
    return _runs_dir(root) / run_id / "events.jsonl"


def _resolve_log(root: Path, run_id: str) -> Path | None:
    """Find the ``events.jsonl`` for ``run_id``.

    The emitter writes to ``runs/<run_id>/events.jsonl`` so the directory name
    is normally the run id — try that first. Fall back to scanning for a log
    whose events carry this ``run_id`` (fixtures use a friendly directory name
    such as ``demo_run``).
    """

    direct = _run_log_path(root, run_id)
    if direct.is_file():
        return direct
    runs_dir = _runs_dir(root)
    if not runs_dir.is_dir():
        return None
    for run_dir in sorted(runs_dir.iterdir()):
        log = run_dir / "events.jsonl"
        if not log.is_file():
            continue
        events = schemas.load_run_log(log)
        if events and events[0].get("run_id") == run_id:
            return log
    return None


def _load_events(root: Path, run_id: str) -> list[dict] | None:
    """Return a run's events in ``seq`` order, or ``None`` if it has no log."""

    log = _resolve_log(root, run_id)
    if log is None:
        return None
    return schemas.load_run_log(log)


def _first(events: list[dict], type_: str) -> dict | None:
    return next((e for e in events if e["type"] == type_), None)


def _last(events: list[dict], type_: str) -> dict | None:
    return next((e for e in reversed(events) if e["type"] == type_), None)


def _summary(events: list[dict]) -> dict:
    """The one-line run summary used by ``GET /api/runs`` and inside a run.

    ``status`` is ``"running"`` until a ``run.finished`` event lands;
    ``best_saving_pct`` is that event's ``saving_pct.mean`` (``None`` while the
    run is still going).
    """

    started = _first(events, "run.started")
    finished = _last(events, "run.finished")
    sdata = started["data"] if started else {}
    run_id = started["run_id"] if started else (events[0]["run_id"] if events else None)

    status = "running"
    best_saving_pct = None
    if finished:
        status = finished["data"].get("status")
        best_saving_pct = (finished["data"].get("saving_pct") or {}).get("mean")

    return {
        "run_id": run_id,
        "started_at": started["ts"] if started else None,
        "mode": sdata.get("mode"),
        "model": sdata.get("model"),
        "status": status,
        "best_saving_pct": best_saving_pct,
    }


def _load_receipt(root: Path, receipt_id: str | None) -> dict | None:
    """Read ``receipts/<id>.json`` under ``root``; ``None`` if absent or unsafe."""

    if not receipt_id or not _RECEIPT_ID_RE.match(receipt_id):
        return None
    path = root / "receipts" / f"{receipt_id}.json"
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _receipt_for(root: Path, events: list[dict]) -> dict | None:
    """The signed receipt a run points at, or ``None`` if unsigned / missing."""

    signed = _last(events, "receipt.signed")
    if not signed:
        return None
    return _load_receipt(root, signed["data"].get("receipt_id"))


def _ts_epoch(ts: str) -> float:
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()


def _delay_seconds(prev_ts: str, cur_ts: str, speed: float) -> float:
    """Replay pacing: the recorded gap between two events, scaled by ``speed``.

    ``speed <= 0`` means "as fast as possible" (no wait). Unparseable or
    out-of-order timestamps never block the stream.
    """

    if speed <= 0:
        return 0.0
    try:
        gap = _ts_epoch(cur_ts) - _ts_epoch(prev_ts)
    except ValueError:
        return 0.0
    return max(0.0, gap / speed)


def _sse_frame(event: dict) -> str:
    """One SSE frame: ``id: <seq>\\nevent: <type>\\ndata: <event json>\\n\\n``."""

    return f"id: {event['seq']}\nevent: {event['type']}\ndata: {_dumps(event)}\n\n"


def _resume_seq(request: Request) -> int | None:
    """The ``seq`` to resume after: the ``Last-Event-ID`` header (what an
    ``EventSource`` sends on reconnect), or a ``?last_event_id=`` fallback."""

    raw = request.headers.get("last-event-id")
    if raw is None:
        raw = request.query_params.get("last_event_id")
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _speed(request: Request) -> float:
    try:
        return float(request.query_params.get("speed", "1"))
    except ValueError:
        return 1.0


async def _event_stream(
    events: list[dict], *, after_seq: int | None, speed: float
) -> AsyncIterator[str]:
    """Yield SSE frames for ``events`` with ``seq > after_seq``, paced by
    ``speed``, closing once ``run.finished`` has been sent."""

    prev_ts: str | None = None
    for event in events:
        emit = after_seq is None or event["seq"] > after_seq
        if emit and prev_ts is not None:
            delay = _delay_seconds(prev_ts, event["ts"], speed)
            if delay > 0:
                await asyncio.sleep(delay)
        prev_ts = event["ts"]
        if not emit:
            continue
        yield _sse_frame(event)
        if event["type"] == "run.finished":
            break


def create_app(root: str | os.PathLike[str] = ".") -> FastAPI:
    """Build the API rooted at ``root`` (which holds ``runs/`` and ``receipts/``)."""

    base = Path(root)
    app = FastAPI(title="WattNot read-only API", version="1")

    @app.get("/api/runs")
    def list_runs() -> list[dict]:
        runs_dir = _runs_dir(base)
        summaries: list[dict] = []
        if runs_dir.is_dir():
            for run_dir in sorted(runs_dir.iterdir()):
                log = run_dir / "events.jsonl"
                if not log.is_file():
                    continue
                events = schemas.load_run_log(log)
                if events:
                    summaries.append(_summary(events))
        return summaries

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str) -> Any:
        if not _RUN_ID_RE.match(run_id):
            return JSONResponse({"code": "bad_run_id"}, status_code=400)
        events = _load_events(base, run_id)
        if events is None:
            return JSONResponse({"code": "run_not_found"}, status_code=404)
        return {
            "run_id": run_id,
            "summary": _summary(events),
            "events": events,
            "receipt": _receipt_for(base, events),
        }

    @app.get("/api/runs/{run_id}/events")
    async def stream_run_events(run_id: str, request: Request) -> Any:
        if not _RUN_ID_RE.match(run_id):
            return JSONResponse({"code": "bad_run_id"}, status_code=400)
        events = _load_events(base, run_id)
        if events is None:
            return JSONResponse({"code": "run_not_found"}, status_code=404)
        stream = _event_stream(
            events, after_seq=_resume_seq(request), speed=_speed(request)
        )
        headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
        return StreamingResponse(
            stream, media_type="text/event-stream", headers=headers
        )

    @app.get("/api/receipts/{receipt_id}")
    def get_receipt(receipt_id: str) -> Any:
        receipt = _load_receipt(base, receipt_id)
        if receipt is None:
            return JSONResponse({"code": "receipt_not_found"}, status_code=404)
        return receipt

    @app.get("/api/pareto/{run_id}")
    def get_pareto(run_id: str) -> Any:
        if not _RUN_ID_RE.match(run_id):
            return JSONResponse({"code": "bad_run_id"}, status_code=400)
        events = _load_events(base, run_id)
        if events is None:
            return JSONResponse({"code": "run_not_found"}, status_code=404)
        latest = _last(events, "pareto.updated")
        points = latest["data"]["points"] if latest else []
        return {"run_id": run_id, "points": points}

    return app


# Module-level app for ``uvicorn wattnot.api.app:app``. ``WATTNOT_ROOT`` points
# at the directory holding ``runs/`` and ``receipts/`` (defaults to the cwd).
app = create_app(os.environ.get("WATTNOT_ROOT", "."))
