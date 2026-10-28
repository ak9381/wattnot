import json

from wattnot.health import check_all, log_fallback

ENV = {"META_API_KEY": "k", "BASETEN_API_KEY": "k", "MODAL_ENDPOINT_URL": "https://m.example",
       "MODAL_PROXY_TOKEN_ID": "i", "MODAL_PROXY_TOKEN_SECRET": "s"}


def test_all_ok():
    led = check_all(env=ENV, opener=lambda r, t: (200, json.dumps(
        {"data": [{"id": "muse-spark-1.3"}, {"id": "Muse-Spark-Open-816B"}]}).encode()))
    assert led["all_ok"] and led["fallbacks"] == []


def test_404_403_and_unset_fall_back():
    def op(req, t):
        if "baseten" in req.full_url:
            return 403, b""
        if "meta" in req.full_url:
            return 404, b""
        raise TimeoutError
    led = check_all(env=ENV, opener=op)
    by = {r["id"]: r for r in led["entries"]}
    assert by["baseten"]["why"].startswith("forbidden") and by["meta"]["http"] == 404
    assert by["modal"]["status"] == "down" and len(led["fallbacks"]) == 3
    assert all(f.startswith("FALLBACK:") for f in led["fallbacks"])
    led2 = check_all(env={"META_API_KEY": "k"}, opener=lambda r, t: (200, b"{}"), only=("modal",))
    assert led2["entries"][0]["status"] == "unconfigured"


def test_model_missing_and_runtime_log():
    led = check_all(env=ENV, opener=lambda r, t: (200, b'{"data":[{"id":"other"}]}'), only=("baseten",))
    assert led["entries"][0]["status"] == "model_missing"
    log_fallback(led, "planner", "Spark Open timed out, bisect used")
    assert led["fallbacks"][-1] == "FALLBACK: Spark Open timed out, bisect used"


def test_key_never_in_ledger():
    led = check_all(env={**ENV, "BASETEN_API_KEY": "SECRET123"}, opener=lambda r, t: (500, b""))
    assert "SECRET123" not in json.dumps(led)
