from wattnot.context_saver import (
    ChunkStore,
    ContextSaver,
    MemoryCompressionMiddleware,
    MockSummarizer,
    ToolDedupMiddleware,
    conversation_tokens,
    estimate_tokens,
    quality_gate,
    saver_receipt_block,
)


def test_estimate_tokens_and_conversation_tokens():
    assert estimate_tokens("") == 0
    assert estimate_tokens("a" * 40) == 10
    msgs = [{"role": "user", "content": "a" * 40},
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "c1", "function": {"name": "read", "arguments": "b" * 20}}]}]
    assert conversation_tokens(msgs) == 10 + estimate_tokens("read" + "b" * 20)


def test_tool_dedup_replaces_repeat_reads_with_a_reference():
    big = "X" * 2000
    msgs = []
    for i in range(3):
        msgs.append({"role": "assistant", "content": "",
                     "tool_calls": [{"id": f"c{i}",
                                     "function": {"name": "read_file",
                                                  "arguments": '{"path":"a.py"}'}}]})
        msgs.append({"role": "tool", "tool_call_id": f"c{i}", "content": big})
    out, st = ToolDedupMiddleware().apply(msgs)
    # first read kept in full; the next two referenced
    tool_msgs = [m for m in out if m.get("role") == "tool"]
    assert tool_msgs[0]["content"] == big
    assert st["deduped"] == 2
    assert all("identical to earlier" in m["content"] for m in tool_msgs[1:])
    assert st["tokens_after"] < st["tokens_before"]


def test_tool_dedup_matches_identical_content_even_without_args():
    body = "same result text " * 50
    msgs = [{"role": "tool", "tool_call_id": "x", "content": body},
            {"role": "tool", "tool_call_id": "y", "content": body}]
    out, st = ToolDedupMiddleware().apply(msgs)
    assert st["deduped"] == 1 and "identical to earlier" in out[1]["content"]


def test_memory_compression_summarises_old_turns_and_retrieves():
    summ = MockSummarizer()
    mw = MemoryCompressionMiddleware(summarize=summ, keep_last=2, min_old=2, store=ChunkStore())
    msgs = [{"role": "system", "content": "system prompt"}]
    for i in range(6):
        msgs.append({"role": "user", "content":
                     f"Turn {i} about the power cap controller and nvml energy sampling detail {i}."})
    msgs.append({"role": "user", "content": "Tell me about the power cap controller."})
    out, st = mw.apply(msgs)
    assert st["applied"] and st["summarize_calls"] > 0
    assert st["tokens_after"] < st["tokens_before"]
    # system prompt is preserved and a compressed recap was inserted
    assert out[0]["content"] == "system prompt"
    assert any("Earlier context (compressed" in (m.get("content") or "") for m in out)
    assert len(mw.store) == st["chunks_made"]


def test_memory_compression_noop_when_few_turns():
    mw = MemoryCompressionMiddleware(keep_last=4, min_old=2)
    msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "hi"}]
    out, st = mw.apply(msgs)
    assert st["applied"] is False and out == msgs


def test_memory_compression_never_orphans_a_tool_reply():
    # The kept window would start on a `tool` reply whose assistant `tool_calls` message sits in
    # the summarised-away half. Dropping the call but keeping the reply orphans it (HTTP 400). The
    # split must move back so the whole call/reply pair is kept together.
    mw = MemoryCompressionMiddleware(summarize=MockSummarizer(), keep_last=3, min_old=2,
                                     store=ChunkStore())
    msgs = [
        {"role": "system", "content": "system prompt"},
        {"role": "user", "content": "first question about the energy cap"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "call_a", "function": {"name": "read", "arguments": '{"p":"a"}'}}]},
        {"role": "tool", "tool_call_id": "call_a", "content": "early file a"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "call_b", "function": {"name": "read", "arguments": '{"p":"b"}'}}]},
        {"role": "tool", "tool_call_id": "call_b", "content": "recent file b"},
        {"role": "assistant", "content": "an answer about energy"},
        {"role": "user", "content": "latest question about the energy cap"},
    ]
    out, st = mw.apply(msgs)
    assert st["applied"]
    # Every kept tool reply is answered by an assistant tool_call that is also kept (no orphan).
    seen_call_ids: set[str] = set()
    for m in out:
        if m.get("role") == "assistant":
            for c in m.get("tool_calls") or []:
                seen_call_ids.add(str(c.get("id")))
        if m.get("role") == "tool":
            assert str(m.get("tool_call_id")) in seen_call_ids, "orphaned tool reply would 400"
    # call_b's pair was kept together; call_a's pair was summarised away as a unit.
    assert any(m.get("role") == "tool" and m.get("tool_call_id") == "call_b" for m in out)
    assert not any(m.get("role") == "tool" and m.get("tool_call_id") == "call_a" for m in out)


def test_context_saver_chain_accounts_for_tokens():
    big = "File contents. " * 100
    msgs = [{"role": "system", "content": "sys"}]
    for i in range(5):
        msgs.append({"role": "assistant", "content": "",
                     "tool_calls": [{"id": f"c{i}",
                                     "function": {"name": "read", "arguments": '{"p":"a"}'}}]})
        msgs.append({"role": "tool", "tool_call_id": f"c{i}", "content": big})
        msgs.append({"role": "user", "content": f"step {i} about energy"})
    msgs.append({"role": "user", "content": "final question about energy"})
    out, acct = ContextSaver().transform(msgs)
    assert acct["tokens_after"] < acct["tokens_before"]
    assert acct["saved_pct"] > 0 and acct["label"] == "ESTIMATE"
    assert [s["name"] for s in acct["stages"]] == ["tool_dedup", "memory_compression"]


def test_quality_gate_rolls_back_on_more_than_one_point_drop():
    assert quality_gate(96.0, 95.5)["kept"] is True        # 0.5 pt drop ok
    assert quality_gate(96.0, 95.0)["kept"] is True         # exactly 1 pt ok
    g = quality_gate(96.0, 94.0)                            # 2 pt drop -> rollback
    assert g["kept"] is False and g["drop_pts"] == 2.0 and "rolled back" in g["reason"]


def test_saver_receipt_block_has_before_after_and_honest_labels():
    off = {"tokens": 1000, "tokens_label": "ESTIMATE", "joules": 150.0, "joules_label": "MOCK",
           "pass_pct": 96.0, "pass_pct_label": "MOCK", "cache_hit_rate": None}
    on = {"tokens": 600, "tokens_label": "ESTIMATE", "joules": 90.0, "joules_label": "MOCK",
          "pass_pct": 95.5, "pass_pct_label": "MOCK", "cache_hit_rate": None}
    b = saver_receipt_block(off, on)
    assert b["before"]["tokens"] == {"value": 1000, "label": "ESTIMATE"}
    assert b["after"]["joules"] == {"value": 90.0, "label": "MOCK"}
    assert b["tokens_saved"] == 400 and b["tokens_saved_pct"] == 40.0
    assert b["kept"] is True  # 0.5 pt drop holds the gate
