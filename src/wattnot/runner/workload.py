"""Workload sets for the knob runner: the public replay set and Muse Code v1.

FR-KNOB-03: a trial replays a fixed, public workload. There are two of them and
they share one on-disk format and one loader:

* ``workloads/replay-v1.jsonl``   — the public replay set (open prompts only).
* ``workloads/muse-code-v1.jsonl`` — the Muse Code task set v1.

Both are generated once by a maintainer-run script (``scripts/gen_workloads.py``)
and then committed, so every test and gate run replays the same bytes. Nothing
here calls a model API; this module only reads the committed files.

Format
------
One JSON object per line (JSONL). Blank lines and lines whose first non-space
character is ``#`` are ignored, so a file can carry a ``# TODO`` header. Every
item object must have:

* ``id``         — short unique string, stable across regenerations.
* ``prompt``     — the text sent to the model; must be non-empty.
* ``max_tokens`` — positive integer cap on generated tokens.

Any other keys (``source``, ``tags``, ``language``, ...) are kept verbatim in
``WorkloadItem.meta`` so the set can grow without a format change.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

__all__ = [
    "Workload",
    "WorkloadError",
    "WorkloadItem",
    "load_muse_code_set",
    "load_replay_set",
    "load_workload",
    "workloads_dir",
]

_VERSION_RE = re.compile(r"-(v\d+)$")


class WorkloadError(Exception):
    """Raised when a workload file is missing, malformed or self-inconsistent."""


@dataclass(frozen=True)
class WorkloadItem:
    """One replayable request: a prompt plus its generation cap and metadata."""

    id: str
    prompt: str
    max_tokens: int
    meta: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Workload:
    """An ordered, named set of :class:`WorkloadItem` with unique ids.

    ``name`` is the file stem (e.g. ``replay-v1``); ``version`` is the trailing
    ``vN`` of the name if present; ``kind`` labels the family (``replay`` or
    ``muse-code``). Iterating or indexing yields items in file order.
    """

    name: str
    kind: str
    items: tuple[WorkloadItem, ...]
    version: str | None = None

    def __len__(self) -> int:
        return len(self.items)

    def __iter__(self) -> Iterator[WorkloadItem]:
        return iter(self.items)

    def __getitem__(self, index: int) -> WorkloadItem:
        return self.items[index]

    def by_id(self, item_id: str) -> WorkloadItem:
        for item in self.items:
            if item.id == item_id:
                return item
        raise KeyError(item_id)


def _parse_item(obj: object, where: str) -> WorkloadItem:
    if not isinstance(obj, dict):
        raise WorkloadError(f"{where}: each line must be a JSON object")

    data = dict(obj)

    item_id = data.pop("id", None)
    if not isinstance(item_id, str) or not item_id:
        raise WorkloadError(f"{where}: 'id' must be a non-empty string")

    prompt = data.pop("prompt", None)
    if not isinstance(prompt, str) or not prompt:
        raise WorkloadError(f"{where}: 'prompt' must be a non-empty string")

    max_tokens = data.pop("max_tokens", None)
    # bool is a subclass of int; a positive integer is required, not True/False.
    if not isinstance(max_tokens, int) or isinstance(max_tokens, bool) or max_tokens <= 0:
        raise WorkloadError(f"{where}: 'max_tokens' must be a positive integer")

    return WorkloadItem(id=item_id, prompt=prompt, max_tokens=max_tokens, meta=data)


def load_workload(path: str | Path, *, kind: str | None = None) -> Workload:
    """Load and validate one JSONL workload file.

    ``kind`` labels the family; when omitted it is inferred from the name prefix
    (``replay`` or ``muse-code``), falling back to the full name. Raises
    :class:`WorkloadError` on any malformed line, a duplicate id, or an empty
    set; the message names ``path:lineno`` so a bad file is easy to find.
    """

    path = Path(path)
    name = path.stem
    version_match = _VERSION_RE.search(name)
    version = version_match.group(1) if version_match else None

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise WorkloadError(f"{path}: cannot read workload: {exc}") from exc

    items: list[WorkloadItem] = []
    seen: set[str] = set()
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        where = f"{path}:{lineno}"
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            raise WorkloadError(f"{where}: invalid JSON: {exc}") from exc
        item = _parse_item(obj, where)
        if item.id in seen:
            raise WorkloadError(f"{where}: duplicate id {item.id!r}")
        seen.add(item.id)
        items.append(item)

    if not items:
        raise WorkloadError(f"{path}: no items (file is empty or only comments)")

    if kind is None:
        kind = "muse-code" if name.startswith("muse-code") else name.split("-")[0]

    return Workload(name=name, kind=kind, items=tuple(items), version=version)


@lru_cache(maxsize=1)
def workloads_dir() -> Path:
    """Find the repo-root ``workloads/`` directory by walking up from here.

    Mirrors ``wattnot.schemas.schema_dir`` so lookup works in an editable
    install without hard-coding a relative depth.
    """

    for parent in Path(__file__).resolve().parents:
        candidate = parent / "workloads"
        if (candidate / "replay-v1.jsonl").is_file():
            return candidate
    raise WorkloadError("could not locate the repo-root workloads/ directory")


def load_replay_set() -> Workload:
    """Load the committed public replay set (``workloads/replay-v1.jsonl``)."""

    return load_workload(workloads_dir() / "replay-v1.jsonl", kind="replay")


def load_muse_code_set() -> Workload:
    """Load the committed Muse Code task set v1 (``workloads/muse-code-v1.jsonl``)."""

    return load_workload(workloads_dir() / "muse-code-v1.jsonl", kind="muse-code")
