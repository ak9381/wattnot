import io
import json

from wattnot.e2e.fullstack import provider_usage


class _R(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _opener(req, timeout=0):
    if "usage" in req.full_url:
        return _R(json.dumps({"items": [{"results": [{"model": "meta-models/muse-spark-open", "request_count": 6,
               "input_tokens": 1346, "cached_input_tokens": 640, "output_tokens": 2127}]}]}).encode())
    return _R(json.dumps({"items": [{"date": "2026-11-01", "results": [{"subtotal": "2.5"}]}]}).encode())


def test_crosscheck_matches_and_is_measured(monkeypatch):
    monkeypatch.setenv("BASETEN_METRICS_KEY", "zzSECRETzz")
    own = {"requests": 6, "input_tokens": 1346, "cached_input_tokens": 640, "output_tokens": 2127}
    r = provider_usage("2026-11-01T11:26:00Z", "2026-11-01T11:27:10+00:00", own, opener=_opener, tries=1)
    assert r["label"] == "MEASURED" and r["match"] and r["worst_diff_pct"] == 0.0
    assert r["billing"]["subtotal_usd"] == 2.5
    assert "zzSECRETzz" not in json.dumps(r)


def test_no_key_skips(monkeypatch):
    monkeypatch.delenv("BASETEN_METRICS_KEY", raising=False)
    assert provider_usage("2026-11-01T11:26:00Z", "2026-11-01T11:27:00+00:00", {})["status"] == "skipped"
