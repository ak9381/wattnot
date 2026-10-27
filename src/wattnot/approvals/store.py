"""Where proposals live on disk, and the audit trail.

    <root>/
      proposals/<id>.json     one file per proposal (the current record)
      audit.jsonl             append-only, hash-chained: every state change, who, when, how
      policy.json             the auto-approve policy (absent or disabled = off)
      active/<kind>.json      what is currently applied for config-style kinds

Writes take a file lock and replace files atomically, so the CLI, the HTTP server and a
running tuning session can use the same folder at the same time.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import tempfile
from collections.abc import Iterator
from pathlib import Path

from wattnot.approvals import model as m

try:  # POSIX only; on other systems the lock is a no-op (single-user tool)
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]

ENV_ROOT = "WATTNOT_PROPOSALS_DIR"
DEFAULT_ROOT = "proposals"
_ID_RE = re.compile(r"^prop_[0-9]{8}_[0-9]{6}_[0-9a-f]{8}$|^[A-Za-z0-9_-]{1,80}$")
GENESIS = "0" * 64


def resolve_root(root: str | os.PathLike | None = None) -> Path:
    return Path(root or os.environ.get(ENV_ROOT) or DEFAULT_ROOT)


class NotFound(KeyError):
    pass


class Store:
    def __init__(self, root: str | os.PathLike | None = None) -> None:
        self.root = resolve_root(root)
        self.dir = self.root / "proposals"
        self.audit_path = self.root / "audit.jsonl"
        self.dir.mkdir(parents=True, exist_ok=True)

    # ---- locking
    @contextlib.contextmanager
    def lock(self) -> Iterator[None]:
        path = self.root / ".lock"
        with path.open("a+") as fh:
            if fcntl is not None:
                fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                yield
            finally:
                if fcntl is not None:
                    fcntl.flock(fh, fcntl.LOCK_UN)

    # ---- proposals
    def _path(self, pid: str) -> Path:
        if not _ID_RE.match(pid):
            raise NotFound(pid)
        return self.dir / f"{pid}.json"

    def exists(self, pid: str) -> bool:
        try:
            return self._path(pid).is_file()
        except NotFound:
            return False

    def get(self, pid: str) -> dict:
        p = self._path(pid)
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise NotFound(pid) from None

    def put(self, proposal: dict) -> None:
        self._atomic(self._path(proposal["id"]), json.dumps(proposal, indent=2) + "\n")

    def all(self) -> list[dict]:
        out = []
        for f in sorted(self.dir.glob("*.json")):
            try:
                out.append(json.loads(f.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue  # a half-written or foreign file is skipped; `verify` reports it
        out.sort(key=lambda p: (p.get("created_at", ""), p.get("id", "")), reverse=True)
        return out

    def _atomic(self, path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".part")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(text)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise

    def write_text(self, rel: str, text: str) -> Path:
        path = self.root / rel
        self._atomic(path, text)
        return path

    # ---- audit chain
    def audit_entries(self) -> list[dict]:
        if not self.audit_path.is_file():
            return []
        out = []
        for line in self.audit_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                out.append(json.loads(line))
        return out

    def audit_append(self, *, ts: float, proposal_id: str | None, event: str, actor: str,
                     via: str, detail: dict | None = None,
                     content_sha256: str | None = None) -> dict:
        """Append one entry. Call with the lock held."""
        entries = self.audit_entries()
        prev = entries[-1]["sha256"] if entries else GENESIS
        e = {"seq": len(entries) + 1, "ts": m.iso(ts), "proposal_id": proposal_id,
             "event": event, "actor": actor, "via": via, "detail": detail or {},
             "content_sha256": content_sha256, "prev_sha256": prev}
        e["sha256"] = m.sha256_hex(m.canon(e))
        with self.audit_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(e, separators=(",", ":")) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        return e

    def audit_for(self, pid: str) -> list[dict]:
        return [e for e in self.audit_entries() if e.get("proposal_id") == pid]

    def verify_chain(self) -> list[str]:
        """Problems with the audit chain and with proposals that disagree with it (empty = ok)."""
        problems: list[str] = []
        prev = GENESIS
        last_event: dict[str, dict] = {}
        try:
            entries = self.audit_entries()
        except ValueError as e:
            return [f"audit.jsonl is not valid JSON lines: {e}"]
        for i, e in enumerate(entries, 1):
            body = {k: v for k, v in e.items() if k != "sha256"}
            if e.get("seq") != i:
                problems.append(f"audit entry {i}: seq is {e.get('seq')}")
            if e.get("prev_sha256") != prev:
                problems.append(f"audit entry {i}: chain broken (prev hash does not match)")
            if m.sha256_hex(m.canon(body)) != e.get("sha256"):
                problems.append(f"audit entry {i}: content does not match its hash")
            prev = e.get("sha256", "")
            if e.get("proposal_id") and e.get("event") in _STATUS_EVENTS:
                last_event[e["proposal_id"]] = e
        for p in self.all():
            if m.content_hash(p) != p.get("content_sha256"):
                problems.append(f"{p['id']}: reviewed content was edited after it was proposed")
            le = last_event.get(p["id"])
            want = _STATUS_EVENTS.get(le["event"]) if le else None
            if le and le["event"] == "grant_closed":
                want = (le.get("detail") or {}).get("status_at_close")
            if want and p.get("status") != want:
                problems.append(f"{p['id']}: status is {p.get('status')} but the audit trail "
                                f"ends at {le['event']}")
            if not le:
                problems.append(f"{p['id']}: no audit entries")
        return problems


# The audit event that sets each status.
_STATUS_EVENTS = {"proposed": m.PENDING, "approved": m.APPROVED, "rejected": m.REJECTED,
                  "expired": m.EXPIRED, "applied": m.APPLIED, "apply_failed": m.FAILED,
                  "rolled_back": m.ROLLED_BACK,
                  "grant_closed": m.CLOSED,  # a revoked grant stays revoked: see verify_chain
                  "grant_opened": m.APPLIED, "revoked": m.ROLLED_BACK}
