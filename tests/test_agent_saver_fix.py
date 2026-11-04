"""Agent 60 on the H100 (4 Nov, seq-20261104-002729-agent): the first 3 shadow-checked agent calls
used 745+118 tokens vs 694+45 at stock (MEASURED, fixture below) and the gate switched off
agent_waste, routing and thinking_cap together. Causes and fixes tested here:
* the guard's nudge appended a note AND the done tool schema to an already-long looping trace
  (+51 prompt tokens). Lean nudge: drop the repeated identical tool turns, one-line note, no done tool.
* a losing pair switched off every applied saver. Now only the likely culprit(s) go off.
* agent turns compared on tool name + canonical args; a loop-breaking text answer vs stock repeating
  a call it already made is not scored as a disagreement."""
import json
from pathlib import Path

from wattnot.agentwaste.config import WasteConfig
from wattnot.agentwaste.guard import WasteGuard, collapse_repeats
from wattnot.gateway.gate import SaverGate, blame
from wattnot.gateway.monitor import agree_turn, loop_break
from wattnot.gateway.traffic import _agent_body

FIX = Path(__file__).parent / "fixtures" / "h100_agent60_shadow.jsonl"
PAIRS = [json.loads(line) for line in FIX.read_text().splitlines() if line.strip()]
ALL3 = ["agent_waste", "routing", "thinking_cap"]


def _size(body):
    return len(json.dumps(body.get("messages")) + json.dumps(body.get("tools")))


def test_lean_nudge_never_longer_than_stock_on_the_h100_agent_trace():
    stock = _agent_body()                       # the exact trace the Agent 60 run replayed
    old = WasteGuard(WasteConfig()).before(stock).body
    new = WasteGuard(WasteConfig(lean_nudge=True)).before(stock).body
    assert _size(old) > _size(stock)            # the bug: note + done tool on top of the full loop
    assert _size(new) < _size(stock)            # fixed: repeats removed, so shorter than stock
    assert [t["function"]["name"] for t in new["tools"]] == ["search"]   # tool schemas untouched
    assert new["tools"] == stock["tools"]
    assert new["messages"][-1]["role"] == "user" and "answer now" in new["messages"][-1]["content"]


def test_healthy_agent_call_is_byte_identical():
    body = _agent_body()
    body["messages"] = body["messages"][:3]     # one search, no loop
    v = WasteGuard(WasteConfig(lean_nudge=True)).before(body)
    assert json.dumps(v.body, sort_keys=True) == json.dumps(body, sort_keys=True)


def test_collapse_keeps_calls_with_new_results():
    msgs = _agent_body()["messages"]
    msgs[4] = {**msgs[4], "content": "different result"}   # second call got something new
    out, dropped = collapse_repeats(msgs)
    assert dropped == 2 and any(m.get("content") == "different result" for m in out)


def test_gate_with_logged_pairs_switches_off_only_the_prompt_grower():
    g = SaverGate(min_pairs=3)
    for p in PAIRS:
        g.record_pair("glimmer", p["small_in"] + p["small_out"], p["base_in"] + p["base_out"], p["agree"],
                      ALL3, small_in=p["small_in"], base_in=p["base_in"])
    off = {e["saver"] for e in g.status()["off"]}
    assert off == {"agent_waste"}, g.status()   # prompt +51 x 3: only the saver that adds text
    assert g.allow("routing", "glimmer") and g.allow("thinking_cap", "glimmer")


def test_gate_blames_effort_lever_when_prompt_is_not_longer():
    g = SaverGate(min_pairs=3)
    for _ in range(3):   # same prompt, +73 output: the reasoning_effort lever, not the guard
        g.record_pair("glimmer", 694 + 118, 694 + 45, True, ["routing", "thinking_cap", "context_saver"],
                      small_in=694, base_in=694)
    off = {e["saver"] for e in g.status()["off"]}
    assert off == {"routing", "thinking_cap"} and g.allow("context_saver", "glimmer")


def test_blame_falls_back_to_all_without_split():
    assert blame(ALL3, [{"small": 1, "base": 0, "agree": False}])[0] == ALL3
    assert blame(["routing"], [])[0] == ["routing"]


def _call(name, args):
    return {"type": "function", "function": {"name": name, "arguments": args}}


def test_agent_agreement_uses_tool_name_and_normalised_args():
    a = [_call("search", '{"q": "H100 power limit", "n": 3}')]
    b = [_call("search", '{"n":3,"q":"H100 power limit"}')]
    assert agree_turn("", a, "", b)
    assert not agree_turn("", a, "", [_call("search", '{"q": "A100"}')])
    assert not agree_turn("", a, "", [_call("done", '{"reason": "x"}')])
    assert not agree_turn("", [], "", [])          # two empty texts never count as agreeing


def test_loop_break_is_not_scored_as_disagreement():
    body = _agent_body()
    stock = [_call("search", '{"q":"H100 power limit"}')]   # stock repeats the same call a 5th time
    assert loop_break(body, "The H100 default power limit is 700 W.", None, stock)
    assert not loop_break(body, "700 W", None, [_call("search", '{"q": "new"}')])
    assert not loop_break(body, "", None, stock)
    g = SaverGate(min_pairs=3)
    for _ in range(3):   # shorter request, loop ended: agent_waste stays on
        g.record_pair("m", 400 + 30, 694 + 45, None, ["agent_waste"], small_in=400, base_in=694)
    assert g.allow("agent_waste", "m")
