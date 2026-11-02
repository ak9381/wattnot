"""Long-conversation gate: short conversations pass through exactly as with the saver off."""
import copy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "e2e"))
from wattnot.context_saver.middleware import MemoryCompressionMiddleware, conversation_tokens  # noqa: E402


class CountingSummarizer:
    model = "count"

    def __init__(self):
        self.calls = 0

    def __call__(self, text):
        self.calls += 1
        return text[:20]


def short_conv(n_turns=8):
    msgs = [{"role": "system", "content": "You are a careful agent."},
            {"role": "user", "content": "What is 17 * 23? Use the tools."}]
    for i in range(n_turns):
        msgs.append({"role": "assistant", "content": f"I will compute part {i} of the product carefully now."})
        msgs.append({"role": "user", "content": f"RESULT {i * 17} and a short note about step {i}."})
    return msgs


def test_short_conversation_is_untouched_and_costs_nothing():
    s = CountingSummarizer()
    mw = MemoryCompressionMiddleware(summarize=s)
    msgs = short_conv()
    assert conversation_tokens(msgs) < mw.min_tokens
    snap = copy.deepcopy(msgs)
    out, rec = mw.apply(msgs)
    assert out == snap and s.calls == 0
    assert rec["applied"] is False and rec["gated"] == "below_min_tokens" and rec["summarize_calls"] == 0


def test_long_conversation_still_compressed():
    s = CountingSummarizer()
    mw = MemoryCompressionMiddleware(summarize=s)
    msgs = short_conv(4)
    msgs[3]["content"] = "A long wordy explanation of the approach. " * 80
    msgs[5]["content"] = "More long wordy reasoning about the next step. " * 80
    assert conversation_tokens(msgs) >= mw.min_tokens
    out, rec = mw.apply(msgs)
    assert rec["applied"] and rec["tokens_after"] < rec["tokens_before"] and s.calls > 0


def test_replay_still_11_of_11_with_savings():
    import io
    import contextlib
    import replay_complex_saver as RC
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        RC.main([])
    txt = buf.getvalue()
    news = [ln for ln in txt.splitlines() if ln.strip().startswith("new ")]
    assert len(news) == 4 and all("11/11" in ln and "turns 81" in ln for ln in news)
    saved = [float(ln.split("saved ")[1].split("%")[0]) for ln in news]
    assert saved[2] > 10 and saved[3] > 10   # verbose styles keep their MOCK savings
