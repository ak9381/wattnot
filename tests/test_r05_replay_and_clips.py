import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "scripts" / "e2e")]


def test_replay_complex_items_fixed_saver_no_looping():
    import replay_complex_saver as RP
    ids = [f"complex-{i:02d}" for i in range(11)]
    for style in RP.STYLES:
        new = RP.run(ids, "new", style)
        off = RP.run(ids, "off", style)
        assert all(r["ok"] for r in new), [r for r in new if not r["ok"]]
        assert all(r["no_task_replies"] == 0 for r in new)
        assert sum(r["turns"] for r in new) == sum(r["turns"] for r in off)   # no extra (looping) turns


def test_replay_reproduces_r04e_failure_with_old_saver():
    import replay_complex_saver as RP
    old = RP.run(["complex-00", "complex-01"], "old", "stateless")
    assert not any(r["ok"] for r in old) and all(r["no_task_replies"] > 0 for r in old)


def test_short_tool_results_kept_verbatim():
    from wattnot.context_saver.middleware import MemoryCompressionMiddleware
    seen = []
    mw = MemoryCompressionMiddleware(summarize=lambda t: seen.append(t) or "x", keep_last=2, min_old=1)
    msgs = [{"role": "user", "content": "Task: do it"},
            {"role": "assistant", "content": 'CALL inventory {"item": "a"}'},
            {"role": "user", "content": "RESULT 37"},
            {"role": "assistant", "content": "long " * 100},
            {"role": "user", "content": "RESULT 5"}, {"role": "assistant", "content": "ok"}]
    out, st = mw.apply(msgs)
    recap = next(m["content"] for m in out if str(m["content"]).startswith("Earlier"))
    assert "RESULT 37" in recap and 'CALL inventory {"item": "a"}' in recap
    assert len(seen) == 1   # only the long turn went to the summariser


def test_stage_pushes_results_before_clips_and_refuses_keys(tmp_path):
    import modal_stage as MS
    r = tmp_path / "r09"
    r.mkdir()
    (r / "round.json").write_text("{}")
    calls = []
    ok = MS.push_results(r, "r09", run=lambda st, **k: calls.append(st) or type("R", (), {"returncode": 0})())
    assert ok["pushed"] and calls[-1][:2] == ["git", "push"]
    assert any("user.name=ak9381" in c for st in calls for c in st)
    (r / "x.pem").write_text("k")
    assert not MS.push_results(r, "r09", run=lambda *a, **k: 1/0)["pushed"]
    src = (ROOT / "scripts" / "e2e" / "modal_stage.py").read_text()
    main = src[src.index("def main():"):]
    assert main.index("[results-ready]") < main.index("finish_clips(") 
    assert main.index("push_results(") < main.index("finish_clips(")
    assert "convert=False" in main


def test_smart_saver_keeps_facts_and_beats_safe_on_wordy_stateless_agent():
    import replay_complex_saver as RP
    from wattnot.context_saver.middleware import key_facts
    facts, prose = key_facts('I will now check the stock level carefully. It is wise.\n'
                             'CALL subagent {"task": "cost for a (to 100 units). Report it."}\n'
                             "See src/x.py for details. Error: boom\n```\nprint(1)\n```")
    assert 'CALL subagent {"task": "cost for a (to 100 units). Report it."}' in facts
    assert any("src/x.py" in f for f in facts) and any("print(1)" in f for f in facts)
    assert "check the stock level" in prose and "100" not in prose
    ids = ["complex-01", "complex-04"]
    assert all(r["ok"] for r in RP.run(ids, "new", "verbose-stateless"))
    assert not any(r["ok"] for r in RP.run(ids, "safe", "verbose-stateless"))
