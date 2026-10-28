from wattnot.context_saver import ContextSaver, receipt_parts, saver_receipt_block
from wattnot.context_saver import prefix_cache as PC

KEYS = {"tokens_before", "tokens_after", "joules_before", "joules_after", "gate_pass_pct", "label"}


def _msgs():
    big = "FILE a.py:\n" + ("x = 1\n" * 300)
    m = [{"role": "system", "content": "agent"}]
    for i in range(5):
        m.append({"role": "assistant", "content": "", "tool_calls": [
            {"id": f"c{i}", "function": {"name": "read_file", "arguments": '{"path": "a.py"}'}}]})
        m.append({"role": "tool", "tool_call_id": f"c{i}", "content": big})
        m.append({"role": "user", "content": f"step {i}"})
    return m


def test_parts_shape_for_dashboard():
    _, acct = ContextSaver().transform(_msgs())
    parts = receipt_parts(acct, prefix_ab=PC.mock_ab(3), pass_before=96, pass_after=96, label="MOCK")
    assert set(parts) == {"prefix", "memcomp", "dupcut"}
    for p in parts.values():
        assert KEYS <= set(p)
    assert "hit_rate" in parts["prefix"] and parts["prefix"]["hit_rate"] == 0.82
    assert parts["dupcut"]["tokens_after"] < parts["dupcut"]["tokens_before"]
    blk = saver_receipt_block({"tokens": 1}, {"tokens": 1}, parts=parts)
    assert blk["parts"]["memcomp"]["label"] == "MOCK"


def test_parts_pending_without_data():
    parts = receipt_parts({"stages": []})
    assert all(p["label"] == "PENDING" for p in parts.values())
    assert parts["prefix"]["hit_rate"] is None
