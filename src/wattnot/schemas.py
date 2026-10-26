"""Load and validate the frozen WattNot contracts.

Three JSON Schemas live at the repo root under ``schemas/``:

* ``run-event.schema.json`` — one object per line in ``runs/<id>/events.jsonl``
* ``receipt.schema.json``   — one signed receipt per file
* ``ui-view.schema.json``   — derived, unsigned UI blocks (never hashed)

Everything downstream (emitter, API, verifier, EU export, frontend) reads
these. This module is the single place that compiles them into validators so
no caller re-implements the rules. Schemas are frozen: change them only through
a task that bumps ``schema_version``.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from functools import lru_cache
from pathlib import Path

from jsonschema import Draft202012Validator
from jsonschema import ValidationError as ValidationError  # re-export for callers

__all__ = [
    "SCHEMA_NAMES",
    "SchemaError",
    "SeqError",
    "ValidationError",
    "load_all_schemas",
    "load_run_log",
    "load_schema",
    "schema_dir",
    "validate_event",
    "validate_receipt",
    "validate_run_log",
    "validate_ui_view",
    "validator_for",
]

# Logical name -> file name at the repo root ``schemas/`` directory.
SCHEMA_NAMES: dict[str, str] = {
    "event": "run-event.schema.json",
    "receipt": "receipt.schema.json",
    "ui-view": "ui-view.schema.json",
}


class SchemaError(Exception):
    """Raised when a schema file cannot be found or a name is unknown."""


class SeqError(Exception):
    """Raised when a run log's ``seq`` values are not strictly increasing."""


@lru_cache(maxsize=1)
def schema_dir() -> Path:
    """Find the repo-root ``schemas/`` directory.

    Walk up from this file until a directory holds the frozen run-event
    schema. This keeps the lookup working in an editable install without
    hard-coding a relative depth.
    """

    for parent in Path(__file__).resolve().parents:
        candidate = parent / "schemas"
        if (candidate / SCHEMA_NAMES["event"]).is_file():
            return candidate
    raise SchemaError("could not locate the repo-root schemas/ directory")


@lru_cache(maxsize=len(SCHEMA_NAMES))
def load_schema(name: str) -> dict:
    """Return the parsed schema for a logical ``name`` (``event`` etc.)."""

    try:
        filename = SCHEMA_NAMES[name]
    except KeyError:
        raise SchemaError(
            f"unknown schema {name!r}; known: {sorted(SCHEMA_NAMES)}"
        ) from None
    return json.loads((schema_dir() / filename).read_text())


def load_all_schemas() -> dict[str, dict]:
    """Return every logical name mapped to its parsed schema."""

    return {name: load_schema(name) for name in SCHEMA_NAMES}


@lru_cache(maxsize=len(SCHEMA_NAMES))
def validator_for(name: str) -> Draft202012Validator:
    """Return a compiled, format-checking validator for ``name``."""

    schema = load_schema(name)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(
        schema, format_checker=Draft202012Validator.FORMAT_CHECKER
    )


def validate_event(event: dict) -> None:
    """Validate one run event. Raises ``ValidationError`` if invalid."""

    validator_for("event").validate(event)


def validate_receipt(receipt: dict) -> None:
    """Validate one receipt. Raises ``ValidationError`` if invalid."""

    validator_for("receipt").validate(receipt)


def validate_ui_view(view: dict) -> None:
    """Validate one derived UI view. Raises ``ValidationError`` if invalid."""

    validator_for("ui-view").validate(view)


def load_run_log(path: str | Path) -> list[dict]:
    """Parse a ``events.jsonl`` run log into a list of event objects.

    Blank lines are skipped so a trailing newline is harmless.
    """

    return list(_iter_jsonl(Path(path)))


def _iter_jsonl(path: Path) -> Iterator[dict]:
    with path.open(encoding="utf-8") as handle:
        for lineno, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as exc:
                raise SchemaError(f"{path}:{lineno}: invalid JSON: {exc}") from exc


def validate_run_log(path: str | Path) -> list[dict]:
    """Validate a whole run log and return its events.

    Checks each event against the schema and that ``seq`` starts at 0 and is
    strictly increasing. Raises ``ValidationError`` or ``SeqError``.
    """

    events = load_run_log(path)
    validator = validator_for("event")
    for event in events:
        validator.validate(event)
    _check_seq(events, path)
    return events


def _check_seq(events: list[dict], path: str | Path) -> None:
    prev: int | None = None
    for event in events:
        seq = event["seq"]
        if prev is None:
            if seq != 0:
                raise SeqError(f"{path}: first seq is {seq}, expected 0")
        elif seq <= prev:
            raise SeqError(f"{path}: seq {seq} does not increase past {prev}")
        prev = seq
