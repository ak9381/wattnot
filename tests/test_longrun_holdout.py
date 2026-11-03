import json
from pathlib import Path

import pytest

from wattnot.longrun import driver as D
from wattnot.longrun import holdout as H


def test_committed_seal_verifies():
    v = H.verify()
    assert v["ok"] and v["n"] == 977


def test_pool_is_disjoint_from_every_tuning_set():
    items = [json.loads(ln) for ln in H.WORKLOAD.read_text().splitlines()]
    idx = {int(x["source"].rsplit("-", 1)[1]) for x in items}
    used = H.used_indices()
    assert not idx & used
    assert min(idx) >= 300                      # first 300 = LangChain p2 / public bench
    proof = {int(json.loads(ln)["source"].rsplit("-", 1)[1])
             for ln in (H.ROOT / "workloads/proof-v1.jsonl").read_text().splitlines()
             if "gsm8k-test-" in ln}
    assert not idx & proof
    man = json.loads(H.MANIFEST.read_text())
    assert not idx & set(man["excluded"]["text_hits_in_repo"])


def _tmp_build(tmp_path, n=20):
    paths = dict(workload=tmp_path / "w.jsonl", manifest=tmp_path / "m.json", seal=tmp_path / "s")
    H.build(n=n, **paths)
    return paths


def test_build_seal_and_tamper_detection(tmp_path):
    p = _tmp_build(tmp_path)
    assert H.verify(**p)["n"] == 20
    with pytest.raises(SystemExit):
        H.build(n=5, **p)                       # already sealed: no silent redraw
    lines = p["workload"].read_text().splitlines()
    x = json.loads(lines[0]); x["answer"] = "#### 0"; lines[0] = json.dumps(x)
    p["workload"].write_text("\n".join(lines) + "\n")
    with pytest.raises(SystemExit, match="workload changed"):
        H.verify(**p)


def test_stack_change_breaks_seal(tmp_path, monkeypatch):
    p = _tmp_build(tmp_path)
    monkeypatch.setitem(H.FROZEN_STACK, "cap_w", 350)
    with pytest.raises(SystemExit, match="stack config"):
        H.verify(**p)


def test_one_shot_marker(tmp_path):
    H.claim_once(tmp_path / "o", "abc")
    with pytest.raises(SystemExit, match="already opened"):
        H.claim_once(tmp_path / "o", "abc")


def test_fake_run_labels_mock_and_refuses_second_open(tmp_path):
    res = H.run("fake", tmp_path / "r", "m", fake=True)
    assert res["label"] == "MOCK" and res["report"].startswith("Sealed holdout (MOCK")
    assert res["quality"]["n"] == 977
    with pytest.raises(SystemExit):
        H.run("fake", tmp_path / "r", "m", fake=True)


def test_mde_numbers():
    assert H.mcnemar_n(0.024, 0.02) == 469
    assert H.mcnemar_n(0.06, 0.02) == 1175
    assert H.mcnemar_mde(977, 0.06) == pytest.approx(0.0219, abs=5e-4)
    assert H.mcnemar_mde(222, 0.024) != H.mcnemar_mde(222, 0.024)   # nan: not detectable at this psi
    assert H.mcnemar_mde(444, 0.024) == pytest.approx(0.0205, abs=5e-4)
    assert H.mcnemar_exact_p(0, 0) == 1.0
    assert H.mcnemar_exact_p(10, 0) < 0.01


def test_paired_summary_and_report():
    stock = [True] * 90 + [False] * 10
    stack = [True] * 88 + [False] * 2 + [True] * 1 + [False] * 9
    s = H.paired_summary(stock, stack)
    assert s["discordant"] == {"stock_only": 2, "stack_only": 1, "rate": 0.03}
    assert s["diff_pts"] == pytest.approx(-1.0)
    line = H.report_line(s, -27.0)
    assert "MEASURED" in line and "never saw" in line and "-27.0% J/request" in line


def test_driver_holdout_replaces_p1r_only_when_enabled(tmp_path, monkeypatch):
    monkeypatch.delenv("WATTNOT_MAX_HOLDOUT", raising=False)
    ids = [b.id for b in D.plan_glimmer_max(tmp_path)]
    assert "p1r-sweep" in ids and "h1-holdout" not in ids
    monkeypatch.setenv("WATTNOT_MAX_HOLDOUT", "1")
    bl = D.plan_glimmer_max(tmp_path)
    ids = [b.id for b in bl]
    assert "h1-holdout" in ids and not any(i.startswith("p1r-") for i in ids)
    h = next(b for b in bl if b.id == "h1-holdout")
    assert "--fake" not in h.live and "--fake" in h.dry and h.host
    assert ids.index("h1-holdout") > ids.index("p2-agentwaste")
