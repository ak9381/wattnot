import pytest

from wattnot.route.stack import StackedTransport, stack_row, trim_body


def test_trim_keeps_last_user_and_drops_repeats():
    body = {"messages": [{"role": "system", "content": "s"}, {"role": "tool", "content": "x" * 5000},
                         {"role": "tool", "content": "x" * 5000}, {"role": "user", "content": "q\n\n\n\nq"}]}
    t = trim_body(body)
    assert len(t["messages"]) == 3
    assert t["messages"][-1]["content"] == "q\n\n\n\nq"
    assert "[trimmed" in t["messages"][1]["content"]


def test_stacked_transport_counts_tokens():
    st = StackedTransport(lambda b: {"usage": {"prompt_tokens": 100}})
    st({"messages": [{"role": "tool", "content": "a" * 9000}, {"role": "user", "content": "q"}]})
    assert st.tokens_after == 100 and st.tokens_before > 100 and st.label == "ESTIMATE"
    st2 = StackedTransport(lambda b: {})
    st2({"messages": [{"role": "user", "content": "hello there"}]})
    assert st2.label == "ESTIMATE" and st2.tokens_before == st2.tokens_after


def test_savings_compose():
    r = stack_row(tokens_before=1000, tokens_after=630, tokens_label="MEASURED",
                  j_per_token_change_pct=-27.1, caps_label="MEASURED", usd_per_mtok=1.0)
    e = r["energy_saved_pct"]
    assert e["tokens_only"] == 37.0 and e["caps_only"] == 27.1
    assert e["stacked"] == round(100 * (1 - 0.63 * 0.729), 2) and e["label"] == "MEASURED"
    assert r["usd_saved_pct"]["value"] == 37.0
    with pytest.raises(ValueError):
        stack_row(tokens_before=0, tokens_after=0, tokens_label="MEASURED",
                  j_per_token_change_pct=0, caps_label="MEASURED")
