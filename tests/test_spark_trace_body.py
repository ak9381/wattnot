"""Spark trace sends a capped reasoning effort so the answer is not eaten by hidden reasoning."""
from wattnot.e2e import live


def test_spark_trace_body(monkeypatch, tmp_path):
    sent = []

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def __call__(self, body, purpose=""):
            sent.append(body)
            return {"id": "t", "choices": [{"message": {"content": "ok"}}]}
    import wattnot.meta.client as mc
    monkeypatch.setattr(mc, "GuardedClient", FakeClient)
    r = live.spark_trace(tmp_path, {"accuracy_with": 0.9}, None)
    assert r["ok"] == 4 and len(sent) == 4
    assert all(b["reasoning_effort"] == "low" and b["max_tokens"] >= 4000 for b in sent)
