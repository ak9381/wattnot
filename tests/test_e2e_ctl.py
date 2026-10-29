"""scripts/e2e/ctl.py must import cleanly (no API calls, no post helper needed) and must hold
no hardcoded secrets/keys/tokens or credentialled IPs — every credential comes from the env.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

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


def test_no_hardcoded_secrets():
    src = CTL.read_text(encoding="utf-8")
    hits = [(p.pattern[:40], m.group(0)) for p in _SECRET_PATTERNS
            for m in p.finditer(src)]
    assert not hits, f"possible hardcoded secret(s) in {CTL}: {hits}"
    # the two credential names appear only as env-var references, never as literal assignments
    assert not re.search(r"""E2E_API_(?:KEY|TOKEN)\s*=\s*['"]""", src)
