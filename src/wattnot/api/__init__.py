"""Read-only HTTP API over the run event log.

The CLI writes ``runs/<run_id>/events.jsonl`` through
:class:`wattnot.events.Emitter`; this package serves it back to the frontend,
live or replayed. No endpoint starts GPU work (FR-EVT-04): the disk log is the
truth and the API is only a view of it.
"""

from __future__ import annotations

from wattnot.api.app import create_app

__all__ = ["create_app"]
