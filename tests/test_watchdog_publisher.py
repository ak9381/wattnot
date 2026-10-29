"""The off-box heartbeat publisher: pushes to a dedicated branch (or gist) via the git CLI.

Every test runs against a LOCAL BARE git repo on disk — no network, no token, no GitHub. The
push target is injected via `remote=`, so the exact same code path a live run takes (init ->
orphan branch -> commit -> force push) is exercised, just pointed at a `file://` bare repo.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from wattnot.longrun.watchdog.publisher import (
    HeartbeatPublisher,
    default_raw_url,
    publisher_from_env,
)

HB = {"ts": 1000.0, "launch_ts": 0.0, "deadline_ts": 64800.0, "spent_inr": 451.2, "block": "g1"}


def _have_git() -> bool:
    try:
        subprocess.run(["git", "--version"], capture_output=True)
        return True
    except Exception:  # noqa: BLE001
        return False


pytestmark = pytest.mark.skipif(not _have_git(), reason="git CLI not available")


def _bare(tmp_path):
    """A local bare repo to push into, plus a monotonic clock we control."""
    bare = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
    return bare


def _read_branch(bare, branch, file_name):
    """Read a file's content back out of the bare repo (proves the push landed)."""
    out = subprocess.run(["git", "-C", str(bare), "show", f"{branch}:{file_name}"],
                         capture_output=True, text=True, check=True)
    return out.stdout


# --------------------------------------------------------------------------- dry-run default


def test_default_is_dry_run_and_makes_no_git_call():
    calls = []
    pub = HeartbeatPublisher(repo_slug="o/r", runner=lambda *a, **k: calls.append(a))
    res = pub.publish(HB)
    assert pub.dry_run is True
    assert res["published"] is False and res["would_publish"] is True
    assert calls == []  # nothing shelled out
    assert res["raw_url"] == default_raw_url("o/r")


def test_dry_run_needs_no_token():
    # No token in the env at all: dry-run still reports cleanly, never errors.
    pub = HeartbeatPublisher(repo_slug="o/r", env={}, runner=lambda *a, **k: 1 / 0)
    res = pub.publish(HB, force=True)
    assert res["would_publish"] is True and "error" not in res


# --------------------------------------------------------------------------- real push (local bare repo)


def test_live_push_lands_the_beat_on_the_branch(tmp_path):
    bare = _bare(tmp_path)
    pub = HeartbeatPublisher(repo_slug="o/r", remote=str(bare), dry_run=False,
                             branch="watchdog-heartbeat")
    res = pub.publish(HB, force=True)
    assert res["published"] is True
    got = json.loads(_read_branch(bare, "watchdog-heartbeat", "heartbeat.json"))
    assert got == HB


def test_force_push_replaces_the_single_prior_beat(tmp_path):
    bare = _bare(tmp_path)
    clock = {"t": 0.0}
    pub = HeartbeatPublisher(repo_slug="o/r", remote=str(bare), dry_run=False,
                             min_interval_s=10.0, clock=lambda: clock["t"])
    assert pub.publish({**HB, "block": "a"})["published"] is True
    clock["t"] = 100.0  # past the rate limit
    assert pub.publish({**HB, "block": "b"})["published"] is True
    got = json.loads(_read_branch(bare, "watchdog-heartbeat", "heartbeat.json"))
    assert got["block"] == "b"
    # orphan + force => the branch holds exactly one commit, no accumulated history
    log = subprocess.run(["git", "-C", str(bare), "rev-list", "--count", "watchdog-heartbeat"],
                         capture_output=True, text=True, check=True)
    assert log.stdout.strip() == "1"


# --------------------------------------------------------------------------- rate limiting


def test_rate_limited_between_pushes(tmp_path):
    bare = _bare(tmp_path)
    clock = {"t": 0.0}
    pub = HeartbeatPublisher(repo_slug="o/r", remote=str(bare), dry_run=False,
                             min_interval_s=60.0, clock=lambda: clock["t"])
    assert pub.publish(HB)["published"] is True
    clock["t"] = 30.0  # inside the window
    r = pub.publish(HB)
    assert r["published"] is False and "rate-limited" in r["reason"]
    clock["t"] = 61.0  # past the window
    assert pub.publish(HB)["published"] is True


def test_dry_run_also_honours_rate_limit():
    clock = {"t": 0.0}
    pub = HeartbeatPublisher(repo_slug="o/r", min_interval_s=60.0, clock=lambda: clock["t"])
    assert pub.publish(HB)["would_publish"] is True
    clock["t"] = 5.0
    assert "rate-limited" in pub.publish(HB)["reason"]


# --------------------------------------------------------------------------- failure tolerance


def test_push_to_a_bad_remote_never_raises(tmp_path):
    pub = HeartbeatPublisher(repo_slug="o/r", remote=str(tmp_path / "nope.git"),
                             dry_run=False)
    res = pub.publish(HB, force=True)
    assert res["published"] is False and "error" in res  # reported, not raised


def test_live_without_token_or_remote_refuses_quietly():
    pub = HeartbeatPublisher(repo_slug="o/r", env={}, dry_run=False)
    res = pub.publish(HB, force=True)
    assert res["published"] is False and "no publish token" in res["error"]


def test_token_is_never_echoed_in_errors(tmp_path):
    # A failing runner whose stderr contains the token must be redacted in the result.
    def runner(argv, **kw):
        return subprocess.CompletedProcess(argv, 1, stdout="", stderr="fatal: SECRET-TOKEN bad")

    pub = HeartbeatPublisher(repo_slug="o/r", token="SECRET-TOKEN", dry_run=False, runner=runner)
    res = pub.publish(HB, force=True)
    assert res["published"] is False
    assert "SECRET-TOKEN" not in json.dumps(res)
    assert "***" in res["error"]


def test_remote_url_embeds_token_but_raw_url_is_clean():
    pub = HeartbeatPublisher(repo_slug="o/r", token="TK", dry_run=False)
    assert "x-access-token:TK@github.com/o/r.git" == pub._remote_url().split("//", 1)[1]
    assert pub.raw_url() == "https://raw.githubusercontent.com/o/r/watchdog-heartbeat/heartbeat.json"


# --------------------------------------------------------------------------- gist mode


def test_gist_mode_builds_a_gist_remote(tmp_path):
    bare = _bare(tmp_path)
    pub = HeartbeatPublisher(mode="gist", gist_id="abc123", remote=str(bare), dry_run=False,
                             branch="main")
    res = pub.publish(HB, force=True)
    assert res["published"] is True
    assert json.loads(_read_branch(bare, "main", "heartbeat.json")) == HB


def test_gist_url_shape_and_needs_id():
    pub = HeartbeatPublisher(mode="gist", gist_id="abc123", token="TK", dry_run=False)
    assert pub._remote_url() == "https://x-access-token:TK@gist.github.com/abc123.git"
    assert pub.raw_url() is None  # gist raw URL has an opaque revision; not derivable here
    with pytest.raises(ValueError):
        HeartbeatPublisher(mode="gist", dry_run=False)._remote_url()


# --------------------------------------------------------------------------- env factory


def test_publisher_from_env_branch_and_gist_and_unconfigured():
    assert publisher_from_env({}) is None  # nothing set -> no publishing
    assert publisher_from_env({"WATCHDOG_PUBLISH_MODE": "gist"}) is None  # gist but no id
    p = publisher_from_env({"WATCHDOG_PUBLISH_REPO": "o/r"})
    assert p is not None and p.mode == "branch" and p.repo_slug == "o/r" and p.dry_run is True
    g = publisher_from_env({"WATCHDOG_PUBLISH_MODE": "gist", "WATCHDOG_PUBLISH_GIST_ID": "x"})
    assert g is not None and g.mode == "gist" and g.gist_id == "x"
