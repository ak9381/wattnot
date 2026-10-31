import json

import pytest

from wattnot import datasets as ds


def test_catalog_sets_hash_and_count():
    for k, n in (("humaneval", 40), ("gsm8k", 60), ("mixed", 111)):
        d = ds.describe(k)
        assert d["items"] == n and len(d["sha256"]) == 64


def test_custom_upload_validation(tmp_path):
    good = "\n".join(json.dumps(r) for r in (
        {"prompt": "2+2?", "expected": "4"}, {"prompt": "def f(): ...", "tests": "assert f() is None"}))
    d = ds.save_custom(good, tmp_path)
    assert d["items"] == 2 and d["checks"] == "mixed"
    with pytest.raises(ds.DatasetError, match="expected"):
        ds.parse_upload(json.dumps({"prompt": "hi"}))
    with pytest.raises(ds.DatasetError, match="not valid JSON"):
        ds.parse_upload("{oops")
    with pytest.raises(ds.DatasetError):
        ds.parse_upload("")


def test_decide_picks_closest():
    code = ["def add(a, b):\n    return a + b", "```python\nimport os\n```"] * 10
    math = ["Tom has 5 apples and buys 3 more. How many does he have?"] * 20
    chat = ["Summarise this email for me", "Write a polite reply"] * 10
    assert ds.decide(code + chat[:4])["chosen"] == "humaneval"
    assert ds.decide(math)["chosen"] == "gsm8k"
    d = ds.decide(code[:6] + math[:6] + chat[:8])
    assert d["chosen"] == "mixed" and "60%" in d["why"]
    assert ds.decide(code + math) == ds.decide(code + math)  # deterministic


def test_resolve_records_choice():
    r = ds.resolve("auto", traffic=["Tom has 5 apples and 3 pears. How many in total?"] * 5)
    assert r["chosen_by"] == "wattnot" and r["id"] == "gsm8k" and r["why"]
    assert ds.resolve("gsm8k")["chosen_by"] == "user"
    with pytest.raises(ds.DatasetError):
        ds.resolve("custom")


def test_dashboard_endpoints():
    from fastapi.testclient import TestClient

    from wattnot.dash.server import create_app
    c = TestClient(create_app())
    o = c.get("/api/dash/v2/options").json()
    assert any(x["id"] == "dataset" for x in o["options"])
    r = c.post("/api/dash/v2/dataset/upload", content='{"prompt": "x"}')
    assert r.status_code == 400
    r = c.post("/api/dash/v2/dataset/decide", json={"prompts": ["def f(x):\n  return x"] * 3})
    assert r.json()["chosen"] == "humaneval"
