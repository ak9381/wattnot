"""scripts/e2e/ctl.py must import cleanly (no API calls, no post helper needed) and must hold
no hardcoded secrets/keys/tokens or credentialled IPs — every credential comes from the env.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CTL = ROOT / "scripts" / "e2e" / "ctl.py"


def _load():
    """Import ctl.py by path."""
    spec = importlib.util.spec_from_file_location("e2e_ctl_under_test", CTL)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_imports_without_post_helper(monkeypatch, tmp_path):
    # point the optional `post` helper dir at an empty path and hide any already-imported `post`,
    # so we prove the module imports (runs no API calls) even when the helper is absent.
    monkeypatch.setenv("E2E_POST_DIR", str(tmp_path / "nope"))
    monkeypatch.setitem(sys.modules, "post", None)  # force `from post import call` to fail
    m = _load()
    assert m.call is None  # stays None, no crash — import ran no API call
    assert callable(m.required_balance)
    # the long-run floor logic is pure and testable with no network
    assert m.required_balance(24, 379.05) == max(24 * 379.05, m.LONG_RUN_FLOOR)
    assert m.required_balance(2, 451.2) == 2 * 451.2 + 1500


# literal-secret patterns: an assignment/kv of a key/token/secret/password to a quoted string,
# a Bearer literal, a credentialled URL (user:pass@host), or a literal IPv4 address. The regex
# `\d+\.\d+...` in find_ip is NOT a literal IP (it has no real digits), so it does not match.
_SECRET_PATTERNS = [
    # a *_key / secret / token / password assigned (=) or mapped (:) to a quoted literal
    re.compile(r"""(?i)(?:^|[^A-Za-z])(api[_-]?key|secret|token|password|passwd|pwd"""
               r"""|access[_-]?key)\s*[:=]\s*['"][^'"\s]+['"]"""),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._\-]{8,}"),
    re.compile(r"[A-Za-z][A-Za-z0-9+.\-]*://[^/\s:@]+:[^/\s:@]+@"),  # scheme://user:pass@host
    re.compile(r"(?<![\w.])\d{1,3}(?:\.\d{1,3}){3}(?![\w.])"),       # literal dotted-quad IPv4
]


def test_no_command_exits_2_not_indexerror(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("E2E_POST_DIR", str(tmp_path / "nope"))
    monkeypatch.setitem(sys.modules, "post", None)
    m = _load()
    with pytest.raises(SystemExit) as e:
        m.main([])                       # no command: argparse usage, not IndexError on sys.argv[1]
    assert e.value.code == 2
    assert "usage" in capsys.readouterr().err.lower()


def test_help_lists_the_subcommands(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("E2E_POST_DIR", str(tmp_path / "nope"))
    monkeypatch.setitem(sys.modules, "post", None)
    m = _load()
    with pytest.raises(SystemExit) as e:
        m.main(["--help"])
    assert e.value.code == 0
    out = capsys.readouterr().out
    assert "launch" in out and "watchdog" in out and "delete" in out


def test_unknown_command_exits_2(monkeypatch, tmp_path):
    monkeypatch.setenv("E2E_POST_DIR", str(tmp_path / "nope"))
    monkeypatch.setitem(sys.modules, "post", None)
    m = _load()
    with pytest.raises(SystemExit) as e:
        m.main(["frobnicate"])
    assert e.value.code == 2


def test_no_hardcoded_secrets():
    src = CTL.read_text(encoding="utf-8")
    hits = [(p.pattern[:40], m.group(0)) for p in _SECRET_PATTERNS
            for m in p.finditer(src)]
    assert not hits, f"possible hardcoded secret(s) in {CTL}: {hits}"
    # the two credential names appear only as env-var references, never as literal assignments
    assert not re.search(r"""E2E_API_(?:KEY|TOKEN)\s*=\s*['"]""", src)


def test_booking_is_3h30_and_watchdog_hard_deletes_at_cap_minus_8_min(monkeypatch):
    import datetime as dt
    monkeypatch.delenv("RUN3_MAX_H", raising=False)
    m = _load()
    assert m.MAX_H == 3.5
    # the E2E launch rule (balance must cover the lifetime, never below the 24 h floor)
    assert m.required_balance(3.5, 379.05) == m.LONG_RUN_FLOOR
    launch = dt.datetime(2026, 11, 1, 10, 0, 0)
    deadline = launch + dt.timedelta(hours=m.MAX_H)
    monkeypatch.setattr(m, "vm", lambda: {"id": 1, "launch_ts": launch.isoformat(),
                                         "deadline": deadline.isoformat()})
    calls = []
    monkeypatch.setattr(m, "delete", lambda: calls.append("delete"))
    monkeypatch.setattr(m, "balance", lambda: 9000.0)
    monkeypatch.setattr(m.os.path, "exists", lambda p: False)
    monkeypatch.setattr(m.time, "sleep", lambda s: None)

    class Clock(dt.datetime):
        t = launch + dt.timedelta(hours=3, minutes=21)   # 1 min before cap - 8 min

        @classmethod
        def now(cls, tz=None):
            cls.t += dt.timedelta(minutes=2)
            return cls.t

    monkeypatch.setattr(m.dt, "datetime", Clock)
    monkeypatch.setattr("builtins.open", lambda *a, **k: __import__("io").StringIO())
    m.main(["watchdog"])
    assert calls == ["delete"]           # a hard DELETE (not a stop) once 3 h 22 min passed
    assert Clock.t - launch <= dt.timedelta(hours=3, minutes=26)
