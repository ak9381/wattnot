"""Publish the driver's heartbeat off-box so layers 4 and 5 can read it.

The driver writes `heartbeat.json` on the control box. The off-box layers (the scheduled
GitHub Actions cron and the macOS watchdog) run *somewhere else* and cannot read that local
file, so the run has to push the beat somewhere they can fetch it over HTTP. This module does
exactly that and nothing more: it force-updates a single file on a dedicated, orphan git branch
(the default), which raw.githubusercontent then serves as a stable URL. An optional gist mode
pushes to a gist's git remote instead.

Design rules (all four matter, because this runs inside a long unattended GPU run):

* **dry-run by default** — a `HeartbeatPublisher` created without `dry_run=False` never shells
  out to git and never touches the network. It reports what it *would* push. Publishing is an
  explicit opt-in, exactly like every teardown in this package.
* **failure-tolerant** — `publish()` catches everything. A missing token, no git binary, a
  network error, a rejected push: all return a result dict with an ``error`` field. It never
  raises, so a flaky publish can never crash the run it is supposed to be reporting on.
* **rate-limited** — a push at most every `min_interval_s` (60 s by default). The driver may
  call `publish()` on every block; most calls are cheap no-ops that return ``"rate-limited"``.
* **git CLI + token from env** — the push uses the `git` binary (no extra deps) with a token
  read from the environment (`WATCHDOG_PUBLISH_TOKEN` by default), embedded in the remote URL.
  The token is NEVER logged: results and error strings are redacted.

The token is a repo/gist-scoped credential held only by the runner's environment, never the
repo. A plain checkout with no token stays in dry-run and publishes nothing.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

GITHUB_HOST = "github.com"
GIST_HOST = "gist.github.com"
RAW_HOST = "raw.githubusercontent.com"

DEFAULT_BRANCH = "watchdog-heartbeat"
DEFAULT_FILE = "heartbeat.json"
DEFAULT_TOKEN_ENV = "WATCHDOG_PUBLISH_TOKEN"
DEFAULT_MIN_INTERVAL_S = 60.0

# A fixed, non-personal commit identity so the orphan branch never leaks a developer's name and
# never depends on the runner having a global git identity configured.
_COMMIT_NAME = "watchdog-publisher"
_COMMIT_EMAIL = "watchdog@localhost"


def default_raw_url(repo_slug: str, *, branch: str = DEFAULT_BRANCH,
                    file_name: str = DEFAULT_FILE) -> str:
    """The raw.githubusercontent URL that serves the published beat. This is the value the
    off-box layers read by default. `repo_slug` is "owner/name"."""
    return f"https://{RAW_HOST}/{repo_slug}/{branch}/{file_name}"


def _redact(text: str, *secrets: str) -> str:
    out = text
    for s in secrets:
        if s:
            out = out.replace(s, "***")
    return out


class HeartbeatPublisher:
    """Force-updates one JSON file on a dedicated branch (or a gist) from the run's environment.

    Everything is injectable for tests: `runner` (defaults to subprocess.run), `clock`, `log`,
    and `remote` (so a test can point the push at a local bare repo and never touch a network).
    """

    def __init__(
        self,
        *,
        repo_slug: str | None = None,
        branch: str = DEFAULT_BRANCH,
        file_name: str = DEFAULT_FILE,
        mode: str = "branch",              # "branch" (orphan branch) or "gist"
        gist_id: str | None = None,
        token: str | None = None,
        token_env: str = DEFAULT_TOKEN_ENV,
        remote: str | None = None,         # explicit remote URL (tests pass a bare-repo path)
        min_interval_s: float = DEFAULT_MIN_INTERVAL_S,
        dry_run: bool = True,
        runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
        clock: Callable[[], float] = time.monotonic,
        env: dict | None = None,
        log: Callable[[str], None] = lambda _m: None,
    ) -> None:
        if mode not in ("branch", "gist"):
            raise ValueError(f"mode must be 'branch' or 'gist', not {mode!r}")
        self.repo_slug = repo_slug
        self.branch = branch if mode == "branch" else (branch or "main")
        self.file_name = file_name
        self.mode = mode
        self.gist_id = gist_id
        self.min_interval_s = min_interval_s
        self.dry_run = dry_run
        self.runner = runner
        self.clock = clock
        self.log = log
        env = os.environ if env is None else env
        self.token = token if token is not None else env.get(token_env)
        self._explicit_remote = remote
        self._last_push: float | None = None

    # ------------------------------------------------------------------ remote URL
    def _remote_url(self) -> str:
        """The push URL, with the token embedded. Tests override via `remote=`."""
        if self._explicit_remote is not None:
            return self._explicit_remote
        tok = self.token or ""
        cred = f"x-access-token:{tok}@" if tok else ""
        if self.mode == "gist":
            if not self.gist_id:
                raise ValueError("gist mode needs a gist_id")
            return f"https://{cred}{GIST_HOST}/{self.gist_id}.git"
        if not self.repo_slug:
            raise ValueError("branch mode needs a repo_slug")
        return f"https://{cred}{GITHUB_HOST}/{self.repo_slug}.git"

    def raw_url(self) -> str | None:
        """The read URL the off-box layers should poll, or None if it cannot be derived (e.g. a
        gist, whose raw URL has an opaque revision component best taken from the gist itself)."""
        if self.mode == "branch" and self.repo_slug:
            return default_raw_url(self.repo_slug, branch=self.branch, file_name=self.file_name)
        return None

    # ------------------------------------------------------------------ publish
    def _due(self, now: float, force: bool) -> bool:
        if force or self._last_push is None:
            return True
        return (now - self._last_push) >= self.min_interval_s

    def publish(self, heartbeat: dict, *, force: bool = False) -> dict:
        """Push `heartbeat` (the heartbeat+budget JSON) off-box. Never raises.

        Returns a result dict: ``published`` (bool), plus ``reason``/``error`` describing why a
        push was skipped or failed. In dry-run it reports ``would_publish`` and makes no git call.
        """
        now = self.clock()
        base = {"published": False, "mode": self.mode, "branch": self.branch,
                "dry_run": self.dry_run}
        if not self._due(now, force):
            wait = round(self.min_interval_s - (now - (self._last_push or now)), 1)
            return {**base, "reason": f"rate-limited ({wait}s to next)"}

        if self.dry_run:
            # Mark the slot used so dry-run honours the same cadence a live run would, but make
            # no git call and never require a token.
            self._last_push = now
            return {**base, "would_publish": True,
                    "note": "dry-run: no git call made", "raw_url": self.raw_url()}

        if not self.token and self._explicit_remote is None:
            # No credential and no injected remote: refuse quietly, do not guess.
            return {**base, "error": "no publish token (set the publish-token env var) and no "
                    "explicit remote; staying off"}

        try:
            self._git_push(heartbeat)
        except Exception as e:  # noqa: BLE001 - a publish must never crash the run it reports on
            msg = _redact(f"{type(e).__name__}: {e}", self.token or "")
            self.log(f"heartbeat publish failed: {msg}")
            return {**base, "error": msg}

        self._last_push = now
        return {**base, "published": True, "raw_url": self.raw_url()}

    def _git_push(self, heartbeat: dict) -> None:
        """Build a throwaway single-commit repo on an orphan branch and force-push it.

        Because the work happens in a fresh temp dir every time, the pushed history is always a
        single commit holding a single file — a force push replaces whatever was there. The run's
        own working tree is never touched.
        """
        remote = self._remote_url()
        with tempfile.TemporaryDirectory() as td:
            g = ["git", "-C", td]
            self._run(["git", "init", "-q", td])
            # Orphan branch so the beat carries no unrelated history. On a fresh repo the initial
            # branch is already unborn; --orphan names it and keeps the tree empty.
            self._run(g + ["checkout", "-q", "--orphan", self.branch])
            Path(td, self.file_name).write_text(json.dumps(heartbeat))
            self._run(g + ["add", self.file_name])
            self._run(g + ["-c", f"user.name={_COMMIT_NAME}", "-c", f"user.email={_COMMIT_EMAIL}",
                           "commit", "-q", "-m", "heartbeat"])
            # Force so each push replaces the single prior beat; no fetch/merge is ever needed.
            self._run(g + ["push", "-q", "--force", remote, f"HEAD:{self.branch}"])

    def _run(self, argv: list[str]) -> None:
        r = self.runner(argv, capture_output=True, text=True)
        if getattr(r, "returncode", 0) != 0:
            err = _redact((getattr(r, "stderr", "") or getattr(r, "stdout", "") or "").strip(),
                          self.token or "")
            # argv[-2] may be the remote URL (holds the token) — never echo it raw.
            cmd = argv[0] if argv[0] != "git" else f"git {argv[1] if argv[1] != '-C' else argv[3]}"
            raise RuntimeError(f"{cmd} exited {r.returncode}: {err[:300]}")


def publisher_from_env(env: dict | None = None, *, dry_run: bool = True,
                       **kw) -> HeartbeatPublisher | None:
    """Build a publisher from the runner's environment, or None if nothing is configured.

    Reads WATCHDOG_PUBLISH_MODE (branch|gist), WATCHDOG_PUBLISH_REPO ("owner/name"),
    WATCHDOG_PUBLISH_BRANCH, WATCHDOG_PUBLISH_GIST_ID. Returns None when neither a repo (branch
    mode) nor a gist id (gist mode) is set, so an unconfigured run simply does not publish.
    """
    env = os.environ if env is None else env
    mode = (env.get("WATCHDOG_PUBLISH_MODE") or "branch").strip()
    repo = env.get("WATCHDOG_PUBLISH_REPO")
    gist_id = env.get("WATCHDOG_PUBLISH_GIST_ID")
    branch = env.get("WATCHDOG_PUBLISH_BRANCH") or DEFAULT_BRANCH
    if mode == "gist" and not gist_id:
        return None
    if mode == "branch" and not repo:
        return None
    return HeartbeatPublisher(repo_slug=repo, branch=branch, mode=mode, gist_id=gist_id,
                              env=env, dry_run=dry_run, **kw)
