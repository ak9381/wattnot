"""The read-only E2E API check. No network: a fake opener returns canned GET responses, and the
dry-run path is asserted to make NO call at all. The whole point is that there is no mutating
code path, so a test also asserts a non-GET verb is refused before any request is built."""

from __future__ import annotations

import importlib.util
import io
import json
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[1] / "scripts" / "e2e_api_check.py"
_spec = importlib.util.spec_from_file_location("e2e_api_check", _PATH)
chk = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(chk)


class _Resp:
    def __init__(self, body, status=200):
        self._b = json.dumps(body).encode()
        self.status = status

    def read(self):
        return self._b


def _opener(by_path):
    """Fake opener: route by the request path, record the methods that were used."""
    used = []

    def opener(req):
        used.append((req.get_method(), req.full_url))
        for frag, resp in by_path.items():
            if frag in req.full_url:
                return resp
        return _Resp({"data": []})

    opener.used = used
    return opener


# --------------------------------------------------------------------------- dry-run default


def test_dry_run_is_default_and_makes_no_call():
    op = _opener({})
    out = io.StringIO()
    rc = chk.main([], env={"E2E_API_KEY": "KEY"}, opener=op, log=lambda m: out.write(m))
    assert rc == 0
    assert op.used == []  # nothing hit the (fake) network
    res = json.loads(out.getvalue())
    assert res["dry_run"] is True and res["would_request"][0]["method"] == "GET"


def test_dry_run_redacts_the_apikey_in_the_printed_plan():
    out = io.StringIO()
    chk.main(["--vm-id", "99"], env={"E2E_API_KEY": "SUPERSECRET", "E2E_PROJECT_ID": "18993"},
             opener=_opener({}), log=lambda m: out.write(m))
    printed = out.getvalue()
    assert "SUPERSECRET" not in printed and "apikey=%2A%2A%2A" in printed or "apikey=***" in printed
    assert "gpu/notebooks/99/" in printed  # the per-id GET is planned


def test_dry_run_needs_no_key():
    # No credentials at all: dry-run still works and makes no call.
    rc = chk.main([], env={}, opener=_opener({}), log=lambda m: None)
    assert rc == 0


# --------------------------------------------------------------------------- live read-only


def test_live_performs_only_gets_and_summarises():
    op = _opener({
        "customer/details/": _Resp({"data": {"prepaid_user_balance": "4321.0"}}),
        "gpu/notebooks/123/": _Resp({"data": {"id": 123, "status": "running"}}),
        "gpu/notebooks/": _Resp({"data": [{"id": 123}, {"id": 456}]}),
    })
    out = io.StringIO()
    rc = chk.main(["--live", "--vm-id", "123"], env={"E2E_API_KEY": "K", "E2E_PROJECT_ID": "1"},
                  opener=op, log=lambda m: out.write(m))
    assert rc == 0
    assert all(m == "GET" for m, _ in op.used)  # never anything but GET
    res = json.loads(out.getvalue())
    assert res["checks"]["auth"]["balance_inr"] == 4321.0
    assert res["checks"]["list_nodes"]["count"] == 2
    assert res["checks"]["get_node"]["is_ours"] is True and res["checks"]["get_node"]["found"]


def test_live_refuses_without_a_key():
    rc = chk.main(["--live"], env={}, opener=_opener({}), log=lambda m: None)
    assert rc == 2  # refuses rather than calling with an empty key


def test_live_get_node_404_is_reported_not_raised():
    op = _opener({
        "customer/details/": _Resp({"data": {"prepaid_user_balance": "1.0"}}),
        "gpu/notebooks/777/": _Resp({}, status=404),
    })

    # urllib raises HTTPError on 404; emulate that path.
    import urllib.error

    def opener(req):
        op.used.append((req.get_method(), req.full_url))
        if "gpu/notebooks/777/" in req.full_url:
            raise urllib.error.HTTPError(req.full_url, 404, "gone", {}, None)
        if "customer/details/" in req.full_url:
            return _Resp({"data": {"prepaid_user_balance": "1.0"}})
        return _Resp({"data": []})

    opener.used = op.used
    out = io.StringIO()
    rc = chk.main(["--live", "--vm-id", "777"], env={"E2E_API_KEY": "K"},
                  opener=opener, log=lambda m: out.write(m))
    res = json.loads(out.getvalue())
    assert res["checks"]["get_node"]["status"] == 404
    assert res["checks"]["get_node"]["found"] is False
    assert rc in (0, 1)  # a 404 on a specific id is a report, not a crash


# --------------------------------------------------------------------------- the safety guard


def test_non_get_method_is_refused_at_the_choke_point():
    client = chk.E2EReadOnly(api_key="K")
    with pytest.raises(ValueError) as e:
        client._request("DELETE", "gpu/notebooks/123/")
    assert "read-only" in str(e.value)
    with pytest.raises(ValueError):
        client._request("POST", "gpu/notebooks/")


def test_client_exposes_no_mutating_method():
    # Belt-and-braces: the public surface has no delete/create/post/put anything.
    names = [n for n in dir(chk.E2EReadOnly) if not n.startswith("_")]
    for bad in ("delete", "create", "post", "put", "launch", "destroy", "remove"):
        assert not any(bad in n.lower() for n in names), (bad, names)
