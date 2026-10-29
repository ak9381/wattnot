"""Code Mode sandbox runtime: the lock-down is real, the stub is deterministic, the receipt signs.

These tests actually run the locked-down subprocess and assert each layer refuses what it must:
no network, CPU/memory/wall limits enforced, no writes outside the temp dir. They also check the
typed stub replays recorded outputs deterministically, the before/after labels stay honest, and
the receipt signs + verifies through the repo's existing offline path.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from wattnot.agentwaste.sandbox import measure as M
from wattnot.agentwaste.sandbox import run as R
from wattnot.agentwaste.sandbox import stub as S
from wattnot.agentwaste.sandbox.runner import Limits, run_code
from wattnot.receipt.canon import canonical_sha256
from wattnot.receipt.sign import generate_test_key, sign, verify_offline_bundle

STUB = '''class Tools:
    def __init__(self, p="recordings.json"):
        self.calls = 0
    def echo(self, x):
        self.calls += 1
        return str(x)
'''


def test_happy_path_runs_and_measures():
    r = run_code("answer=[tools.echo(x=i) for i in range(3)]", STUB, {}, Limits())
    assert r.ok and r.exit_status == 0
    assert r.answer == ["0", "1", "2"]
    assert r.tool_calls == 3
    assert r.wall_s >= 0 and r.cpu_s >= 0 and r.peak_rss_kb > 0
    assert r.net_isolation in ("unshare -n", "audit-hook")


def test_network_is_blocked():
    r = run_code('import socket; socket.create_connection(("1.1.1.1", 53), timeout=2); answer=1',
                 STUB, {}, Limits(wall_s=10))
    assert not r.ok
    # Either the audit hook refused it or (with unshare -n) the connect simply cannot route.
    assert r.answer is None


def test_socket_creation_audit_hook_refuses():
    # Even constructing a socket trips the hook when unshare is unavailable; with unshare the
    # empty netns makes any real connection impossible. Either way the program does not succeed.
    r = run_code('import socket; s=socket.socket(); s.connect(("example.com", 80)); answer=1',
                 STUB, {}, Limits(wall_s=10))
    assert not r.ok


def test_write_outside_tmp_is_blocked(tmp_path):
    target = tmp_path / "escape.txt"
    src = f'open({str(target)!r}, "w").write("x"); answer=1'
    r = run_code(src, STUB, {}, Limits())
    assert not r.ok
    assert not target.exists(), "a write outside the sandbox dir must not land"
    assert "write outside sandbox blocked" in r.stderr


def test_write_inside_tmp_is_allowed():
    # Writing a file inside the sandbox's own dir is fine (the program's result.json relies on it).
    r = run_code('open("scratch.txt", "w").write("ok"); answer="wrote"', STUB, {}, Limits())
    assert r.ok and r.answer == "wrote"


def test_cpu_limit_kills_a_busy_loop():
    src = "x = 0\nwhile True:\n    x += 1\nanswer = x"
    r = run_code(src, STUB, {}, Limits(cpu_s=1, wall_s=30))
    assert not r.ok
    assert not r.timed_out, "a CPU-bound loop must be killed by RLIMIT_CPU, not the wall timer"
    assert r.signal is not None
    assert r.cpu_s <= 3.0  # near the 1s limit, not an unbounded spin


def test_wall_timeout_kills_a_sleeper():
    # sleep burns no CPU, so only the wall-clock timer can stop it.
    r = run_code("import time; time.sleep(30); answer=1", STUB, {}, Limits(cpu_s=20, wall_s=2))
    assert r.timed_out and not r.ok
    assert r.wall_s < 6


def test_memory_limit_is_enforced():
    r = run_code("x = bytearray(900 * 1024 * 1024); answer=1", STUB, {},
                 Limits(as_bytes=128 * 1024 * 1024, wall_s=20))
    assert not r.ok
    assert "MemoryError" in r.stderr


# ---- stub determinism -----------------------------------------------------

def _tiny_trace():
    return [
        {"calls": [["search_docs", '{"query":"a"}', "RESULT-A"],
                   ["search_docs", '{"query":"b"}', "RESULT-B"]]},
        {"calls": [["search_docs", '{"query":"a"}', "RESULT-A2-ignored"]]},
    ]


def test_stub_replays_recorded_outputs_deterministically():
    calls = S.collect_calls(_tiny_trace())
    specs = S.infer_specs(calls)
    rec = S.build_recordings(calls)
    stub_src = S.render_stub_source(specs)
    prog = 'answer = [tools.search_docs(query="a"), tools.search_docs(query="b"), tools.search_docs(query="a")]'
    r1 = run_code(prog, stub_src, rec, Limits())
    r2 = run_code(prog, stub_src, rec, Limits())
    assert r1.ok and r2.ok
    assert r1.answer == r2.answer, "same inputs must give the same recorded outputs"
    # The first recorded output for (search_docs, query=a) wins and repeats.
    assert r1.answer[0] == "RESULT-A"
    assert r1.answer[0] == r1.answer[2]
    assert r1.answer[1] == "RESULT-B"


def test_stub_arg_order_does_not_matter():
    calls = S.collect_calls([{"calls": [["t", '{"a":1,"b":2}', "OUT"]]}])
    rec = S.build_recordings(calls)
    specs = S.infer_specs(calls)
    # Keyed by sorted args, so calling with the keys in a different order still hits.
    stub_src = S.render_stub_source(specs)
    r = run_code("answer = tools.t(b=2, a=1)", stub_src, rec, Limits())
    assert r.ok and r.answer == "OUT"


def test_typed_stub_rejects_unknown_argument():
    calls = S.collect_calls([{"calls": [["t", '{"a":1}', "OUT"]]}])
    stub_src = S.render_stub_source(S.infer_specs(calls))
    r = run_code("answer = tools.t(zzz=1)", stub_src, S.build_recordings(calls), Limits())
    assert not r.ok, "a wrong keyword must be a TypeError, like a real typed client"


# ---- tokenizer + energy honesty -------------------------------------------

def test_tokenizer_is_the_repo_chars_over_four_rule():
    assert M.count_tokens("x" * 40) == 10
    assert M.count_tokens("") == 0


def test_energy_is_null_with_a_reason_when_rapl_unreadable():
    j, reason = M.rapl_reading()
    # In this environment RAPL is absent; either way, a null reading must carry a reason string.
    if j is None:
        assert reason and isinstance(reason, str)


# ---- end-to-end build + labels --------------------------------------------

def _candidate_runs():
    trace = _tiny_trace() + [{"calls": [["search_docs", f'{{"query":"q{i}"}}', f"O{i}"]]}
                             for i in range(8)]
    # One run shaped like a Code Mode candidate: big tool list proxy via many round trips.
    return [{"case": "demo-codemode", "trace": trace,
             "prompt_tokens": 9000, "completion_tokens": 1000, "total_tokens": 10000,
             "wall_s": 20.0, "tools_offered": 6}]


def test_build_runs_candidates_and_labels_are_honest():
    res = R.build(_candidate_runs(), now="2026-01-01T00:00:00Z")
    assert res["spec"] == "wattnot.codemode-sandbox/1"
    assert res["candidates"] >= 1
    row = res["rows"][0]
    sb, after = row["sandbox"], row["after"]
    assert sb["ok"] is True
    assert sb["net_isolation"] in ("unshare -n", "audit-hook")
    # Measured fields are labelled MEASURED; code tokens TOKENIZER; model round trip ESTIMATE.
    assert after["sandbox_wall_s"]["label"] == "MEASURED"
    assert after["cpu_s"]["label"] == "MEASURED"
    assert after["code_tokens"]["label"] == "TOKENIZER"
    assert after["model_round_trips"]["label"] == "ESTIMATE"
    # Energy is MEASURED only if RAPL was readable, else pending (null) with a reason note.
    je = after["sandbox_energy_j"]
    assert (je["label"] == "MEASURED" and je["value"] is not None) or \
           (je["value"] is None and je["note"])
    assert row["saved"]["round_trips"]["value"] >= 0


def test_receipt_signs_and_verifies(tmp_path):
    res = R.build(_candidate_runs(), now="2026-01-01T00:00:00Z")
    receipt = R.build_receipt(res, source="unit test", now="2026-01-01T00:00:00Z")
    # The integrity hash covers everything but the integrity block itself.
    assert receipt["integrity"]["sha256"] == canonical_sha256(receipt)
    rp = tmp_path / "rcpt.json"
    rp.write_text(json.dumps(receipt, indent=2) + "\n")
    key = generate_test_key(tmp_path / "key.pem")
    bundle_path = sign(rp, no_rekor=True, key_path=key)
    signed = json.loads(rp.read_text())
    bundle = json.loads(Path(bundle_path).read_text())
    ok, who = verify_offline_bundle(signed, bundle)
    assert ok and who.startswith("offline:")
    # Signing only fills the integrity block; the hash is unchanged.
    assert signed["integrity"]["sha256"] == receipt["integrity"]["sha256"]


def test_shipped_sandbox_results_artifact_is_well_formed():
    p = Path(__file__).resolve().parents[1] / "data/agent-waste/codemode/sandbox_results.json"
    if not p.is_file():
        pytest.skip("sandbox_results.json not generated yet")
    data = json.loads(p.read_text())
    assert data["spec"] == "wattnot.codemode-sandbox/1"
    assert data["rows"], "expected at least one candidate row in the shipped artifact"
    for row in data["rows"]:
        assert row["sandbox"]["net_isolation"] in ("unshare -n", "audit-hook")
        assert row["after"]["code_tokens"]["label"] == "TOKENIZER"
