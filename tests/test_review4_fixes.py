"""Review round 4: the approval policy lives at a persistent per-user path, never /tmp."""
import importlib

from wattnot.longrun import driver


def test_policy_default_is_persistent_and_overridable(monkeypatch, tmp_path):
    assert not driver.APPROVAL_POLICY.startswith("/tmp")
    assert driver.APPROVAL_POLICY.endswith("wattnot/approval-policy.json")
    monkeypatch.setenv("WATTNOT_APPROVAL_POLICY", str(tmp_path / "p.json"))
    try:
        assert importlib.reload(driver).APPROVAL_POLICY == str(tmp_path / "p.json")
    finally:
        monkeypatch.delenv("WATTNOT_APPROVAL_POLICY")
        importlib.reload(driver)


def test_watchdog_retries_a_failed_delete(monkeypatch):
    import datetime as dt
    import io
    from pathlib import Path
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "ctl_r4", Path(__file__).resolve().parents[1] / "scripts" / "e2e" / "ctl.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    past = dt.datetime(2020, 1, 1)
    monkeypatch.setattr(m, "vm", lambda: {"id": 1, "launch_ts": past.isoformat(),
                                         "deadline": past.isoformat()})
    tries = []

    def flaky():
        tries.append(1)
        if len(tries) == 1:
            raise ConnectionError("api down")
        if len(tries) == 2:
            raise SystemExit(2)   # "NOT CONFIRMED"

    monkeypatch.setattr(m, "delete", flaky)
    monkeypatch.setattr(m.os.path, "exists", lambda p: False)
    monkeypatch.setattr(m.time, "sleep", lambda s: None)
    monkeypatch.setattr("builtins.open", lambda *a, **k: io.StringIO())
    m.main(["watchdog"])
    assert len(tries) == 3
