"""Schema checks for a single run event.

The emitter validates every event here before it touches the disk, so a
malformed event can never reach ``events.jsonl``. This is a thin layer over
:mod:`wattnot.schemas` (the one place that compiles the frozen contracts) plus
a few constants read straight from the schema so they cannot drift:

* ``SCHEMA_VERSION`` — the ``schema_version`` const every event carries.
* ``EVENT_TYPES``    — the allowed ``type`` values.
* ``RUN_ID_RE``      — the compiled ``run_id`` pattern.
"""

from __future__ import annotations

import re

from wattnot import schemas
from wattnot.schemas import SeqError, ValidationError

__all__ = [
    "EVENT_TYPES",
    "RUN_ID_RE",
    "SCHEMA_VERSION",
    "SeqError",
    "ValidationError",
    "check_event",
]


def _event_schema() -> dict:
    return schemas.load_schema("event")


SCHEMA_VERSION: str = _event_schema()["properties"]["schema_version"]["const"]
EVENT_TYPES: frozenset[str] = frozenset(_event_schema()["properties"]["type"]["enum"])
RUN_ID_RE: re.Pattern[str] = re.compile(_event_schema()["properties"]["run_id"]["pattern"])


def check_event(event: dict) -> None:
    """Validate one event against ``run-event.schema.json``.

    Raises :class:`ValidationError` if the event does not match. Delegates to
    :mod:`wattnot.schemas` so there is a single compiled validator process-wide.
    """

    schemas.validate_event(event)
