from wattnot.context_saver import MemoryCompressionMiddleware, SparkSummarizer


class FakeClient:
    def __init__(self, reply=None, boom=False):
        self.reply, self.boom, self.bodies = reply, boom, []

    def __call__(self, body, *, purpose="unspecified"):
        self.bodies.append(body)
        if self.boom:
            raise RuntimeError("down")
        return {"choices": [{"message": {"content": self.reply}}]}


def test_spark_summarizer_uses_muse_and_returns_reply():
    c = FakeClient("short summary")
    s = SparkSummarizer(client=c)
    assert s("A long turn. With more text.") == "short summary"
    assert c.bodies[0]["model"].startswith("muse-spark")
    assert s.calls == 1 and s.failures == 0


def test_spark_summarizer_leaves_reasoning_headroom():
    # Muse Spark spends ~200 reasoning tokens even at minimal effort; too tight a budget returns
    # empty content and silently falls back to the offline mock. Lock in enough headroom.
    c = FakeClient("ok")
    SparkSummarizer(client=c)("A turn to summarise.")
    body = c.bodies[0]
    assert body["reasoning_effort"] == "minimal"
    assert body["max_tokens"] >= 256


def test_spark_summarizer_falls_back_on_error_or_empty():
    for c in (FakeClient(boom=True), FakeClient(reply="")):
        s = SparkSummarizer(client=c)
        assert s("First sentence. Second.") == "First sentence."
        assert s.failures == 1


def test_memory_compression_keeps_tool_pairs_with_spark():
    msgs = [{"role": "system", "content": "sys"}]
    for i in range(4):
        msgs += [{"role": "user", "content": f"u{i}"},
                 {"role": "assistant", "content": None,
                  "tool_calls": [{"id": f"c{i}", "type": "function",
                                  "function": {"name": "read", "arguments": "{}"}}]},
                 {"role": "tool", "tool_call_id": f"c{i}", "content": f"r{i}"}]
    mw = MemoryCompressionMiddleware(summarize=SparkSummarizer(client=FakeClient("sum")), keep_last=4)
    out, stats = mw.apply(msgs)
    ids = {m["tool_call_id"] for m in out if m.get("role") == "tool"}
    calls = {c["id"] for m in out for c in (m.get("tool_calls") or [])}
    assert ids <= calls and stats["model"] == "muse-spark-1.3"
