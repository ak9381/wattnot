"""Off-box backstops (layers 4 and 5): the GitHub Actions engine and the macOS watchdog.

Everything here is dry-run / CPU testable: heartbeats come from a fake opener or a temp file,
teardown goes through the shared `FakeE2EClient`, the keychain lookup is mocked, and `--dry-run`
(the default off-box) never calls the client. No network, no GPU, no VM, no keychain.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from wattnot.longrun.watchdog import FakeE2EClient, FakeVm
from wattnot.longrun.watchdog import core as shared_core
from wattnot.longrun.watchdog import offbox

HOUR = 3600.0
STALE = 900.0


# Load the standalone macOS script by path (it is deliberately not importable as a package).
_MAC_PATH = Path(__file__).resolve().parents[1] / "scripts" / "mac_watchdog" / "watchdog.py"
_spec = importlib.util.spec_from_file_location("mac_watchdog", _MAC_PATH)
mac = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mac)


def _hb(now, trip):
    """A heartbeat dict that trips exactly one cap at t=now and none of the others."""
    d = {"ts": now, "launch_ts": now - HOUR, "deadline_ts": now + 10 * HOUR, "spent_inr": 100.0}
    if trip == "budget":
        d["spent_inr"] = 10_000.0
    elif trip == "time":
        d["deadline_ts"] = now - 1.0
    elif trip == "stale":
        d["ts"] = now - (STALE + 60.0)
    return d


def _opener(payload):
    """A fake URL fetcher returning a fixed body (JSON text, or garbage to test corruption)."""
    body = payload if isinstance(payload, str) else json.dumps(payload)
    return lambda _url: body


# =========================================================================== layer 4: offbox engine


def test_offbox_reuses_shared_decide_and_teardown_on_each_cap():
    now = 1e9
    for trip in ("budget", "time", "stale"):
        vm = FakeVm()
        res = offbox.offbox_check(
            heartbeat_source="https://host/heartbeat.json", client=FakeE2EClient(vm),
            budget_cap_inr=5000, max_hours=5, max_stale_s=STALE, now=now, dry_run=False,
            opener=_opener(_hb(now, trip)), sleep=lambda s: None)
        reason = {"budget": "budget cap", "time": "time cap", "stale": "stale heartbeat"}[trip]
        assert res["teardown"] and reason in res["reasons"]
        assert not vm.present


def test_offbox_dry_run_never_calls_the_client():
    now = 1e9
    client = FakeE2EClient()
    res = offbox.offbox_check(
        heartbeat_source="https://host/hb.json", client=client, budget_cap_inr=5000,
        now=now, dry_run=True, opener=_opener(_hb(now, "budget")))
    assert res["teardown"] is True and "WOULD_DELETE" in res["deleted"]
    assert client.delete_calls == 0 and client.exists_calls == 0 and client.vm.present


def test_offbox_default_is_dry_run():
    now = 1e9
    client = FakeE2EClient()
    # No dry_run argument: off-box must default to dry-run and not act.
    res = offbox.offbox_check(heartbeat_source="https://h/hb.json", client=client,
                              budget_cap_inr=5000, now=now, opener=_opener(_hb(now, "budget")))
    assert res["dry_run"] is True and client.delete_calls == 0


def test_offbox_reads_a_local_file_source_too(tmp_path):
    now = 1e9
    p = tmp_path / "heartbeat.json"
    p.write_text(json.dumps(_hb(now, "time")))
    vm = FakeVm()
    res = offbox.offbox_check(heartbeat_source=str(p), client=FakeE2EClient(vm),
                              budget_cap_inr=1e9, max_hours=5, now=now, dry_run=False,
                              sleep=lambda s: None)
    assert res["teardown"] and "time cap" in res["reasons"] and not vm.present


@pytest.mark.parametrize("payload", [None, "{not json", json.dumps({"no": "ts"})])
def test_offbox_missing_or_corrupt_heartbeat_never_tears_down(payload):
    now = 1e9
    client = FakeE2EClient()
    opener = None if payload is None else _opener(payload)
    src = "https://host/hb.json" if payload is not None else "/no/such/heartbeat.json"
    res = offbox.offbox_check(heartbeat_source=src, client=client, budget_cap_inr=1.0,
                              max_hours=0.0, now=now, dry_run=False, opener=opener)
    assert res["teardown"] is False and client.delete_calls == 0


def test_offbox_two_runners_racing_delete_the_same_vm_once():
    now = 1e9
    vm = FakeVm()
    for _ in range(2):   # layer 4 firing twice (or alongside a Mac) on one VM
        offbox.offbox_check(heartbeat_source="https://h/hb.json", client=FakeE2EClient(vm),
                            budget_cap_inr=5000, now=now, dry_run=False,
                            opener=_opener(_hb(now, "budget")), sleep=lambda s: None)
    assert not vm.present and vm.delete_calls == 1


def test_offbox_live_without_credentials_refuses(monkeypatch):
    monkeypatch.delenv("E2E_API_KEY", raising=False)
    monkeypatch.delenv("E2E_VM_ID", raising=False)
    with pytest.raises(SystemExit):
        offbox.build_live_client_from_env({})


def test_offbox_cli_dry_run_prints_decision(tmp_path, capsys):
    now_hb = tmp_path / "hb.json"
    now_hb.write_text(json.dumps({"ts": 1.0, "launch_ts": 0.0, "deadline_ts": 1e18,
                                  "spent_inr": 99999.0}))
    rc = offbox.main(["--heartbeat-source", str(now_hb), "--budget-cap-inr", "1000"])
    out = json.loads(capsys.readouterr().out.strip())
    assert rc == 0 and out["teardown"] is True and "WOULD_DELETE" in out["deleted"]


def test_offbox_cli_tolerates_empty_cap_strings(tmp_path, capsys):
    # CI passes unset repo variables as "" — argparse must not choke on them.
    p = tmp_path / "hb.json"
    p.write_text(json.dumps(_hb(1e9, "healthy")))   # no cap tripped
    rc = offbox.main(["--heartbeat-source", str(p), "--budget-cap-inr", "", "--max-hours", ""])
    assert rc == 0
    json.loads(capsys.readouterr().out.strip())


# =========================================================================== layer 5: macOS watchdog


def test_mac_decide_matches_the_shared_core_rule():
    """The mac script embeds the rule (stdlib only); it must not drift from core.decide."""
    now = 1000.0
    cases = [
        dict(deadline_ts=now + 10, budget_spent_inr=10, budget_cap_inr=5, heartbeat_ts=now,
             max_stale_s=STALE),                                            # budget
        dict(deadline_ts=now - 1, budget_spent_inr=0, budget_cap_inr=5, heartbeat_ts=now,
             max_stale_s=STALE),                                            # time
        dict(deadline_ts=now + 10, budget_spent_inr=0, budget_cap_inr=5,
             heartbeat_ts=now - STALE - 1, max_stale_s=STALE),              # stale
        dict(deadline_ts=now + 10, budget_spent_inr=1, budget_cap_inr=5, heartbeat_ts=now,
             max_stale_s=STALE),                                            # quiet
        dict(deadline_ts=None, budget_spent_inr=None, budget_cap_inr=None, heartbeat_ts=None,
             max_stale_s=None),                                            # all unenforced
    ]
    for c in cases:
        want = shared_core.decide(now=now, **c)
        got = mac.decide(now=now, **c)
        assert got["teardown"] == want.teardown
        assert got["reasons"] == want.reasons


def test_mac_load_heartbeat_from_url_file_and_bad(tmp_path):
    now = 1e9
    assert mac.load_heartbeat("https://h/hb.json", opener=_opener(_hb(now, "budget")))["ts"] == now
    p = tmp_path / "hb.json"
    p.write_text(json.dumps(_hb(now, "time")))
    assert mac.load_heartbeat(str(p))["deadline_ts"] == now - 1.0
    assert mac.load_heartbeat(str(tmp_path / "missing.json")) is None
    assert mac.load_heartbeat("https://h/hb.json", opener=_opener("{bad")) is None


def test_mac_check_once_tears_down_on_each_cap():
    now = 1e9
    for trip in ("budget", "time", "stale"):
        vm = FakeVm()
        res = mac.check_once(source="https://h/hb.json", client=FakeE2EClient(vm),
                             budget_cap_inr=5000, max_hours=5, max_stale_s=STALE, now=now,
                             dry_run=False, opener=_opener(_hb(now, trip)), sleep=lambda s: None)
        reason = {"budget": "budget cap", "time": "time cap", "stale": "stale heartbeat"}[trip]
        assert res["teardown"] and reason in res["reasons"] and not vm.present


def test_mac_dry_run_never_calls_the_client():
    now = 1e9
    client = FakeE2EClient()
    res = mac.check_once(source="https://h/hb.json", client=client, budget_cap_inr=5000, now=now,
                         dry_run=True, opener=_opener(_hb(now, "budget")))
    assert res["teardown"] and "WOULD_DELETE" in res["deleted"]
    assert client.delete_calls == 0 and client.vm.present


def test_mac_missing_heartbeat_never_tears_down():
    client = FakeE2EClient()
    res = mac.check_once(source="/no/such.json", client=client, budget_cap_inr=1.0,
                         max_hours=0.0, now=1e9, dry_run=False)
    assert res["teardown"] is False and client.delete_calls == 0


def test_mac_double_delete_is_idempotent():
    now = 1e9
    vm = FakeVm()
    for _ in range(3):   # mac racing the on-box layers on one VM
        mac.check_once(source="https://h/hb.json", client=FakeE2EClient(vm), budget_cap_inr=5000,
                       now=now, dry_run=False, opener=_opener(_hb(now, "budget")),
                       sleep=lambda s: None)
    assert not vm.present and vm.delete_calls == 1


def test_mac_teardown_retries_a_flaky_api():
    vm = FakeVm()
    client = FakeE2EClient(vm, fail_times=2)
    td = mac.teardown(client, tries=3, sleep=lambda s: None)
    assert td["deleted"] is True and client.delete_calls == 3 and not vm.present


def test_mac_run_loop_polls_until_a_cap_trips():
    # healthy, then the deadline passes between ticks.
    clock = {"t": 0.0}
    hb = {"ts": 0.0, "launch_ts": 0.0, "deadline_ts": 5.0, "spent_inr": 1.0}

    def opener(_url):
        return json.dumps({**hb, "ts": clock["t"]})

    vm = FakeVm()
    res = mac.run_loop(source="https://h/hb.json", client=FakeE2EClient(vm), budget_cap_inr=1e9,
                       max_hours=None, max_stale_s=None, poll_every_s=2.0, dry_run=False,
                       now=lambda: clock["t"], sleep=lambda s: clock.__setitem__("t", clock["t"] + s),
                       opener=opener, max_ticks=10)
    assert res["teardown"] and "time cap" in res["reasons"] and not vm.present


def test_mac_run_loop_default_clock_is_epoch_not_monotonic():
    # No injected `now`: run_loop must fall back to a wall-clock epoch clock (time.time), because
    # the rule compares `now` against the heartbeat's epoch timestamps. A monotonic default would
    # make `now - heartbeat_ts` nonsense and this real-epoch stale beat would never trip.
    import time as _time
    old = _time.time() - 2 * HOUR                       # a real heartbeat written two hours ago
    hb = {"ts": old, "launch_ts": old, "deadline_ts": old + 100 * HOUR, "spent_inr": 1.0}
    vm = FakeVm()
    res = mac.run_loop(source="https://h/hb.json", client=FakeE2EClient(vm), budget_cap_inr=1e9,
                       max_hours=None, max_stale_s=STALE, dry_run=False,
                       sleep=lambda s: None, opener=_opener(hb), max_ticks=1)
    assert res["teardown"] and "stale heartbeat" in res["reasons"] and not vm.present


# --------------------------------------------------------------------------- keychain (mocked)


def test_mac_keychain_lookup_parses_security_output():
    def runner(argv, **kw):
        assert argv[:2] == ["security", "find-generic-password"]
        return type("P", (), {"returncode": 0, "stdout": "SECRET-KEY\n"})()

    assert mac.keychain_api_key("wattnot-e2e", "e2e-api-key", runner=runner) == "SECRET-KEY"


def test_mac_keychain_missing_returns_none():
    def runner(argv, **kw):
        return type("P", (), {"returncode": 44, "stdout": ""})()   # not found

    assert mac.keychain_api_key("wattnot-e2e", "e2e-api-key", runner=runner) is None

    def boom(argv, **kw):   # no `security` binary at all (not a Mac)
        raise FileNotFoundError("security")

    assert mac.keychain_api_key("s", "a", runner=boom) is None


def test_mac_resolve_credentials_prefers_env_then_keychain():
    kc = lambda s, a: "FROM-KEYCHAIN"
    assert mac.resolve_credentials({"E2E_API_KEY": "FROM-ENV"}, keychain=kc)["api_key"] == "FROM-ENV"
    assert mac.resolve_credentials({}, keychain=kc)["api_key"] == "FROM-KEYCHAIN"
    # auth token also comes from the env, never the repo
    creds = mac.resolve_credentials({"E2E_AUTH_TOKEN": "TOK"}, keychain=lambda s, a: None)
    assert creds["auth_token"] == "TOK" and creds["api_key"] is None


def test_mac_main_dry_run_prints_and_never_builds_a_client(tmp_path, capsys):
    p = tmp_path / "hb.json"
    p.write_text(json.dumps(_hb(1e9, "budget")))
    rc = mac.main(["--heartbeat-source", str(p), "--budget-cap-inr", "5000", "--once"], env={})
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert rc == 0 and out["teardown"] and "WOULD_DELETE" in out["deleted"]


def test_mac_main_defaults_source_to_published_branch(monkeypatch, capsys):
    # No --heartbeat-source: it derives the raw URL of the published branch from the repo slug,
    # then fetches via load_heartbeat. We stub the fetch to confirm the derived URL is used.
    seen = {}

    def fake_load(source, *, opener=None):
        seen["source"] = source
        return None  # a missing beat never tears down; enough to prove the URL was built

    monkeypatch.setattr(mac, "load_heartbeat", fake_load)
    rc = mac.main(["--repo-slug", "o/r", "--once"], env={})
    assert rc == 0
    assert seen["source"] == "https://raw.githubusercontent.com/o/r/watchdog-heartbeat/heartbeat.json"


def test_mac_main_default_source_honours_branch_env(monkeypatch):
    seen = {}
    monkeypatch.setattr(mac, "load_heartbeat",
                        lambda source, **k: seen.update(source=source) or None)
    rc = mac.main(["--once"], env={"WATCHDOG_PUBLISH_REPO": "a/b",
                                   "WATCHDOG_PUBLISH_BRANCH": "beats"})
    assert rc == 0 and seen["source"] == "https://raw.githubusercontent.com/a/b/beats/heartbeat.json"


def test_mac_main_no_source_and_no_slug_refuses():
    assert mac.main(["--once"], env={}) == 2


def test_mac_main_live_refuses_without_a_key(tmp_path, monkeypatch):
    # keychain mocked to "no key"; no env key -> live must refuse, not act.
    monkeypatch.setattr(mac, "keychain_api_key", lambda *a, **k: None)
    p = tmp_path / "hb.json"
    p.write_text(json.dumps(_hb(1e9, "budget")))
    rc = mac.main(["--heartbeat-source", str(p), "--live", "--once"], env={})
    assert rc == 2
