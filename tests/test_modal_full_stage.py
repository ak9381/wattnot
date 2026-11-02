"""Option-B Modal stage: plumbing runs end to end on a fake server, labels stay honest, guards hold."""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load(p):
    spec = importlib.util.spec_from_file_location(p.stem, p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_inner_fake_runs_every_step(tmp_path):
    out = tmp_path / "r"
    subprocess.run([sys.executable, "scripts/e2e/modal_full_inner.py", "--fake", "--limit", "1", "--out", str(out)],
                   cwd=ROOT, check=True, timeout=600)
    doc = json.loads((out / "inner.json").read_text())
    S = doc["steps"]
    for k in ("without", "batch_kv", "fp8", "with", "limits", "blocks"):
        assert k in S, k
    assert set(S["blocks"]) == {"agentwaste", "langchain", "eye", "spoken", "think"}
    assert "MEASURED" not in (out / "inner.json").read_text()      # fake never reads as MEASURED
    # stock stays off; the with arm follows the per-workload switch (short questions -> off)
    pcs = S["prefix_cache_switch"]
    assert S["without_server"]["prefix_caching"] is False
    assert S["with_server"]["prefix_caching"] is pcs["enabled"] is False
    assert pcs["mode"] == "auto" and pcs["prompts"] == doc["items"]
    assert doc["cap_probe"]["writable"] is False


def test_strip_leak():
    m = _load(ROOT / "scripts/e2e/modal_full_inner.py")
    assert m.strip_leak("<think>a</think> 42") == "42"


def test_gate_keeps_only_cheaper_and_accurate():
    m = _load(ROOT / "scripts/e2e/modal_full_inner.py")
    b = {"accuracy": 0.9, "j_per_correct": {"value": 100}}
    assert m.gate(b, {"accuracy": 0.89, "j_per_correct": {"value": 80}})["keep"]
    assert not m.gate(b, {"accuracy": 0.85, "j_per_correct": {"value": 80}})["keep"]
    assert not m.gate(b, {"accuracy": 0.9, "j_per_correct": {"value": 120}})["keep"]


def test_paid_round_needs_go_and_stage_cap(monkeypatch):
    m = _load(ROOT / "scripts/e2e/modal_full_round.py")
    monkeypatch.delenv("WATTNOT_MODAL_GO", raising=False)
    assert m.main(["--round", "rtest"]) == 3
    fs = m.full_stage
    worst = fs.ROUND_CAP_S / 3600 * fs.GPU_USD_H
    assert fs.ROUND_HARD_USD == 10.0
    assert worst <= fs.ROUND_HARD_USD and worst <= 10          # a single round's worst case <= $10
    assert m.STAGE_CAP_USD == 60.0                             # stage cap raised to $60
    assert fs.SMOKE_CAP_S / 3600 * fs.GPU_USD_H <= 1.50


def test_blocks_run_before_fp8(tmp_path):
    """New order: GPU blocks (think first) run before the FP8 gate; FP8 is a BF16-kept candidate."""
    out = tmp_path / "r"
    subprocess.run([sys.executable, "scripts/e2e/modal_full_inner.py", "--fake", "--limit", "1", "--out", str(out)],
                   cwd=ROOT, check=True, timeout=600)
    S = json.loads((out / "inner.json").read_text())["steps"]
    keys = list(S)
    assert keys.index("blocks") < keys.index("fp8")            # blocks step recorded before fp8
    assert list(S["blocks"]) == ["think", "agentwaste", "langchain", "eye", "spoken"]
    assert S["with_server"]["fp8"] is False                    # the with arm this round stays BF16
    assert S["fp8"]["applied"] is False                        # FP8 is a candidate only, never applied


def test_fp8_skipped_low_time_is_not_a_failure(tmp_path):
    """A deadline that leaves < 8 min at FP8 time records a skip (time, not a failure)."""
    out = tmp_path / "r"
    subprocess.run([sys.executable, "scripts/e2e/modal_full_inner.py", "--fake", "--limit", "1",
                    "--deadline-s", "470", "--out", str(out)], cwd=ROOT, check=True, timeout=600)
    d = json.loads((out / "inner.json").read_text())
    fp8 = d["steps"]["fp8"]
    assert fp8["kept"] is False and "skipped" in fp8
    assert "fp8" not in d["failed"] and not d.get("fatal")


def test_server_start_fail_fast_message(tmp_path, monkeypatch):
    """Too little container time -> fail fast before launching vLLM, with the time-left message."""
    import pytest
    m = _load(ROOT / "scripts/e2e/modal_full_inner.py")
    monkeypatch.setattr(m, "left", lambda: 300.0)              # wait = min(900, 120) = 120 < 240
    srv = m.Server("/no/such/model", tmp_path, fake=False)
    with pytest.raises(RuntimeError, match=r"not enough container time to start vLLM .*120s left, need >=240s"):
        srv.start(fp8=False, prefix=False, max_seqs=64)


def test_verify_change_no_spark_is_held(tmp_path, monkeypatch):
    """No-spark run: spark_agreement is skipped (not failed) and the decision is 'held', not 'rejected'."""
    import types
    build = types.ModuleType("wattnot.receipt.build")   # stub so the test needs no jsonschema/receipt stack
    build.verify_hash = lambda r: (True, "")
    rec = types.ModuleType("wattnot.receipt")
    rec.build = build
    monkeypatch.setitem(sys.modules, "wattnot.receipt", rec)
    monkeypatch.setitem(sys.modules, "wattnot.receipt.build", build)
    m = _load(ROOT / "scripts/e2e/modal_full_round.py")
    inner = {"fatal": None, "steps": {
        "without": {"item_latency_s": {"p99": 1.0}, "j_per_correct": {"value": 100}},
        "with": {"item_latency_s": {"p99": 1.0}, "j_per_correct": {"value": 80}},
        "limits": {"p99_ok": True, "joule_slo_ok": True, "quality_gate": {"accuracy_ok": True, "acc_drop_pts": 0.0}},
        "fp8": {"kept": False}}}
    receipt = tmp_path / "r.json"
    receipt.write_text("{}")
    d = m.verify_change(inner, {"label": None, "stopped": "not run"}, receipt, no_spark=True)
    assert d["decision"] == "held"
    assert "spark_agreement" not in d["failed_checks"]
    assert "spark_agreement" in d["skipped_checks"]
    # Spark meant to run but failed stays a rejection.
    d2 = m.verify_change(inner, {"agreement": None, "stopped": "error: boom"}, receipt, no_spark=False)
    assert d2["decision"] == "rejected" and "spark_agreement" in d2["failed_checks"]


def test_sync_results_copies_truncates_and_commits(tmp_path):
    """sync_results mirrors the out dir to the volume, keeps only the last 2 MB of each vLLM log,
    and calls commit (so a 30-s snapshot persists to wattnot-results)."""
    fs = _load(ROOT / "modal/full_stage.py")
    src = tmp_path / "out"
    (src / "raw").mkdir(parents=True)
    (src / "inner.json").write_text('{"ok": 1}')
    (src / "status.json").write_text('{"state": "running"}')
    (src / "raw" / "with.answers.jsonl").write_text("row\n")
    (src / "raw" / "vllm-bf16-pc-64.log").write_bytes(b"A" * 3_000_000)   # oversized server log
    calls = []
    dst = tmp_path / "vol" / "r1"
    fs.sync_results(str(src), str(dst), commit=lambda: calls.append(1))
    assert (dst / "inner.json").read_text() == '{"ok": 1}'
    assert (dst / "status.json").read_text() == '{"state": "running"}'
    assert (dst / "raw" / "with.answers.jsonl").read_text() == "row\n"
    big = (dst / "raw" / "vllm-bf16-pc-64.log").read_bytes()
    assert big.startswith(b"[truncated to the last 2 MB]\n") and len(big) < 2_100_000
    assert calls == [1]                                               # commit was called exactly once


def test_recover_partial_returns_path_when_inner_present(tmp_path):
    """recover_partial returns inc/tag once the fetch has placed an inner.json, else None."""
    m = _load(ROOT / "scripts/e2e/modal_full_round.py")
    inc = tmp_path / "_incoming"
    tag = "smoke-rtest"

    def fake_fetch(t, dest):   # stand in for `modal volume get`: write a partial fixture
        d = Path(dest) / t / "raw"
        d.mkdir(parents=True)
        (Path(dest) / t / "inner.json").write_text(json.dumps(
            {"steps": {"without": {"accuracy": 0.9}, "batch_kv": {"kept": {}}}}))
        (Path(dest) / t / "status.json").write_text('{"state": "inner_timeout"}')

    got = m.recover_partial(tag, inc, fetch=fake_fetch)
    assert got == inc / tag and (got / "inner.json").exists()
    assert "with" not in json.loads((got / "inner.json").read_text())["steps"]
    assert m.recover_partial("nope", inc, fetch=lambda t, d: None) is None   # nothing synced


def test_write_partial_records_finished_steps(tmp_path):
    """write_partial records the finished steps, blocks, failures and spend for a crashed round."""
    m = _load(ROOT / "scripts/e2e/modal_full_round.py")
    rdir = tmp_path / "r"
    rdir.mkdir()
    inner = {"steps": {"without": {"accuracy": 0.9}, "batch_kv": {"kept": {}}, "blocks": {"eye": {"rc": 0}}},
             "failed": {"fp8": "boom"}, "fatal": None}
    stage = {"limit": 1, "modal_usd": {"value": 1.0}}
    d = json.loads(m.write_partial(rdir, inner, stage, "inner_timeout").read_text())
    assert d["status"] == "partial" and d["label"] == "MEASURED"
    assert d["recovered_from"] == "modal volume wattnot-results" and d["state"] == "inner_timeout"
    assert d["finished_steps"] == ["without", "batch_kv", "blocks"]
    assert d["blocks"] == {"eye": {"rc": 0}} and d["failed"] == {"fp8": "boom"}
    assert d["spend"] == stage


def test_smoke_agentwaste_block_is_shortened():
    """Smoke (limit > 0): the agentwaste block is reps 1 / max-cases 4 / max-tokens 768 and its
    wall cap is 240 s; the other blocks cap at 300 s."""
    m = _load(ROOT / "scripts/e2e/modal_full_inner.py")
    cmd = m.block_cmds(4, False, Path("/tmp/out"))["agentwaste"]
    assert "--reps" in cmd and cmd[cmd.index("--reps") + 1] == "1"
    assert "--max-cases" in cmd and cmd[cmd.index("--max-cases") + 1] == "4"
    assert "--max-tokens" in cmd and cmd[cmd.index("--max-tokens") + 1] == "768"
    assert m.block_cap("agentwaste", 4) == 240
    assert all(m.block_cap(k, 4) == 300 for k in ("think", "langchain", "eye", "spoken"))


def test_full_round_agentwaste_block_fits_cap():
    """Full round (limit == 0): r04 timed out at 900 s with reps 2 over all 30 cases, so the full
    round runs 12 cases once per arm at 768 tokens; block limits come from the modal profile."""
    m = _load(ROOT / "scripts/e2e/modal_full_inner.py")
    cmd = m.block_cmds(0, False, Path("/tmp/out"))["agentwaste"]
    assert cmd[cmd.index("--reps") + 1] == "1"
    assert cmd[cmd.index("--max-cases") + 1] == "12" and cmd[cmd.index("--max-tokens") + 1] == "768"
    from wattnot.providers import PROFILES
    lim = PROFILES["modal"]["block_limits_s"]
    assert all(m.block_cap(k, 0) == lim[k] for k in ("agentwaste", "think", "langchain", "eye", "spoken"))


def test_modal_blocks_fit_80pct_and_round_stays_under_10usd():
    from wattnot import providers as pv
    assert pv.block_fit_problems("modal") == []
    prof = pv.PROFILES["modal"]
    # langchain 898 s MEASURED (r04-try1) and agentwaste ~43 min both fit in 80%
    assert 898 <= 0.8 * prof["block_limits_s"]["langchain"]
    assert 43 * 60 <= 0.8 * prof["block_limits_s"]["agentwaste"]
    c = prof["caps"]
    assert c["round_ceiling_min"] / 60 * c["gpu_usd_h"] < c["round_hard_usd"]


def test_block_fit_flags_a_tight_block(monkeypatch):
    from wattnot import providers as pv
    monkeypatch.setitem(pv.PROFILES["modal"], "block_worst_s", {"langchain": 898})
    monkeypatch.setitem(pv.PROFILES["modal"], "block_limits_s", {"langchain": 900})
    assert pv.block_fit_problems("modal")


def test_teardown_never_stops_a_round_owned_by_another_live_process(tmp_path, monkeypatch):
    import os
    import subprocess as sp
    m = _load(ROOT / "scripts/e2e/modal_full_round.py")
    lock = tmp_path / "owner.json"
    monkeypatch.setattr(m, "OWNER", lock)
    calls = []
    monkeypatch.setattr(m.subprocess, "run", lambda *a, **k: calls.append(a) or sp.CompletedProcess(a, 0, "[]", ""))
    lock.write_text(json.dumps({"pid": os.getppid(), "round": "r04c"}))
    td = m.teardown()
    assert td["verified_empty"] is False and "r04c" in td["skipped"] and calls == []
    lock.write_text(json.dumps({"pid": 999999999, "round": "dead"}))   # dead owner: normal teardown
    assert m.foreign_owner() is None


def test_agentwaste_fake_max_cases_runs(tmp_path):
    """agentwaste_gpu_ab --fake --max-cases runs and records the knobs in the summary."""
    out = tmp_path / "aw"
    m = _load(ROOT / "scripts/agentwaste_gpu_ab.py")
    assert m.main(["--fake", "--reps", "1", "--max-cases", "4", "--max-tokens", "768",
                   "--out", str(out)]) == 0
    s = json.loads((out / "agentwaste_gpu_summary.json").read_text())
    assert s["max_cases"] == 4 and s["max_tokens"] == 768


def test_agentwaste_pick_cases_is_deterministic_and_bounded():
    """pick_cases keeps at most N, is deterministic, and spans the kinds (not just normals)."""
    m = _load(ROOT / "scripts/agentwaste_gpu_ab.py")
    import sys as _sys
    _sys.path[:0] = [str(ROOT / "src")]
    from wattnot.agentwaste.bench.cases import all_cases
    cases = all_cases()
    pick = m.pick_cases(cases, 4)
    assert len(pick) == 4
    assert m.pick_cases(cases, 4) == pick                      # deterministic
    assert m.pick_cases(cases, 0) == cases                     # 0 = all
    assert len(m.pick_cases(cases, 10_000)) == len(cases)      # N >= len -> all
    assert {c.category for c in pick} != {"normal"}            # later kinds represented


def test_no_metrics_key_anywhere():
    for p in ("scripts/e2e/modal_full_round.py", "scripts/e2e/modal_full_inner.py", "modal/full_stage.py"):
        assert "BASETEN_METRICS_KEY" not in (ROOT / p).read_text().replace("Never reads BASETEN_METRICS_KEY", "")


def test_untested_paths_fail_soft(tmp_path, monkeypatch):
    """FP8, BudgetChat and a GPU block failing are recorded as FAILED; the round still finishes."""
    import json
    import subprocess
    import sys
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    env = {**__import__("os").environ, "WATTNOT_FAKE_FAIL": "fp8,budget,eye"}
    out = tmp_path / "r"
    subprocess.run([sys.executable, "scripts/e2e/modal_full_inner.py", "--fake", "--out", str(out),
                    "--limit", "1"], cwd=root, env=env, check=True, timeout=600)
    d = json.loads((out / "inner.json").read_text())
    assert "with" in d["steps"] and not d.get("fatal")
    assert set(d["failed"]) >= {"fp8", "thinking_budget", "eye_check"}
    assert d["steps"]["fp8"]["kept"] is False
    sys.path[:0] = [str(root / "src")]
    from wattnot.e2e.fullstack import gpu_feature_table
    t = {r["id"]: r["status"] for r in gpu_feature_table(d["failed"])}
    assert t["fp8"] == t["eye_check"] == "FAILED" and t["power_caps"] == "OFF"
