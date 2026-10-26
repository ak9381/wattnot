"""Run event stream: the emitter that writes it and the schema it checks.

One event log per run (``runs/<run_id>/events.jsonl``) is the contract
between the backend and the UI. The CLI writes it through :class:`Emitter`;
the API (a later task) reads it back, live or replayed.
"""

from __future__ import annotations

from wattnot.events.emitter import Emitter, new_run_id

__all__ = ["Emitter", "new_run_id"]
