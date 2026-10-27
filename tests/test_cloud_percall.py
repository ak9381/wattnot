"""Per-call audit: the receipt against the usage blocks Baseten returned (not the meter)."""

import copy
import io
import json
from pathlib import Path

import pytest

from wattnot import cli
from wattnot.cloud import cli as cloud_cli
from wattnot.cloud import percall as pc
from wattnot.cloud import run as rn
from wattnot.cloud.transport import CloudError
from wattnot.report import cloud as view

NOW = 1_790_000_000.0
CATALOG = {"items": [{"name": "meta-models/muse-spark-open", "cost_per_million_input_tokens": 1.25,
                      "cost_per_million_output_tokens": 4.25}]}


@pytest.fixture(scope="module")
def made(tmp_path_factory):
    d = tmp_path_factory.mktemp("pc")
    from wattnot.cloud.transport import FixtureTransport
    path, r = rn.run(rn.Options(), FixtureTransport.load(rn.DEMO_FIXTURE), "replay-synthetic",
                     out_dir=d, now=NOW)
    rows = [json.loads(x) for x in
            path.with_name(path.stem + ".calls.jsonl").read_text().splitlines()]
    return path, r, rows, json.loads(rn.DEMO_FIXTURE.read_text())


def test_a_clean_receipt_matches_every_check(made):
    _, r, rows, doc = made
    res = pc.audit_per_call(r, rows, doc, simulated=True)
    assert res["status"] == "match" and res["match_pct"] == 100.0
    assert res["label"] == "SIMULATED" and res["source"] == "per-call"
    assert "not the billing meter" in res["scope"]
    assert res["calls_checked"] == res["calls"] == len(rows)
    assert {x["field"] for x in res["rows"]} >= {"requests", "input_tokens", "cached_input_tokens",
                                                 "output_tokens", "reasoning_tokens"}


def test_list_prices_replace_the_receipts_own_input_and_output_rates(made):
    _, r, rows, doc = made
    px = pc.prices_from_catalog(CATALOG, r["model"])  # the catalog name lacks the -1026 suffix
    assert px["input_per_m"] == 1.25 and px["cached_per_m"] is None
    res = pc.audit_per_call(r, rows, doc, prices=px)
    assert res["status"] == "match" and res["price_source"] == "list"
    assert "unconfirmed" in res["price_note"] and res["label"] == "MEASURED"
    px2 = dict(px, output_per_m=5.0)  # a changed list price makes the receipt's USD wrong
    bad = pc.audit_per_call(r, rows, doc, prices=px2)
    assert bad["status"] == "mismatch"
    assert [c for c in bad["checks"] if "cost recomputed" in c["check"]][0]["passed"] == 0
    assert not [x for x in bad["rows"] if x["field"] == "usd_all_calls"][0]["ok"]


def test_a_receipt_that_miscounts_tokens_is_caught(made):
    _, r, rows, doc = made
    rows = copy.deepcopy(rows)
    ok = [x for x in rows if x["status"] == 200]
    ok[3]["completion_tokens"] += 10_000
    ok[5]["cached_tokens"] += 128
    res = pc.audit_per_call(r, rows, doc)
    assert res["status"] == "mismatch" and res["match_pct"] < 100
    names = {f["check"] for f in res["failures"]}
    assert {"completion tokens match", "cached tokens match"} <= names
    assert not [x for x in res["rows"] if x["field"] == "output_tokens"][0]["ok"]


def test_replies_that_contradict_themselves_are_flagged(made):
    _, r, rows, doc = made
    doc = copy.deepcopy(doc)
    tags = [x["tag"] for x in rows if x["status"] == 200]
    a, b, c = (doc["replies"][t]["body"] for t in tags[:3])
    b["id"] = a["id"]  # duplicate response id
    c["usage"]["total_tokens"] += 1
    a["model"] = "someone/else"
    res = pc.audit_per_call(r, rows, doc)
    names = {f["check"] for f in res["failures"]}
    assert {"response id present and unique", "total tokens = prompt + completion",
            "response names our model"} <= names


def test_a_missing_reply_or_header_change_is_reported(made):
    _, r, rows, doc = made
    doc = copy.deepcopy(doc)
    t0, t1 = [x["tag"] for x in rows if x["status"] == 200][:2]
    del doc["replies"][t0]
    doc["replies"][t1]["headers"]["x-ratelimit-limit-requests"] = "1"
    res = pc.audit_per_call(r, rows, doc)
    assert res["status"] == "mismatch" and res["calls_checked"] == res["calls"] - 1
    assert {"call is in the recording", "rate-limit headers match"} <= {
        f["check"] for f in res["failures"]}


def test_find_recording_picks_the_matching_file_and_refuses_otherwise(made, tmp_path):
    _, _, rows, doc = made
    other = tmp_path / "other.json"
    other.write_text(json.dumps({"format": doc["format"], "replies": {}}))
    path, found = pc.find_recording(rows, [other, rn.DEMO_FIXTURE])
    assert path == rn.DEMO_FIXTURE and found["replies"]
    with pytest.raises(CloudError):
        pc.find_recording(rows, [other])


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_fetch_list_prices_uses_the_metrics_key_and_never_echoes_it():
    seen = {}

    def opener(req, timeout):
        seen["auth"] = req.get_header("Authorization")
        seen["url"] = req.full_url
        return _Resp(json.dumps(CATALOG).encode())

    px = pc.fetch_list_prices(env={"BASETEN_METRICS_KEY": "k-abc"}, opener=opener)
    assert seen["url"].endswith("/v1/model_apis") and seen["auth"] == "Bearer k-abc"
    assert px["output_per_m"] == 4.25
    assert "k-abc" not in json.dumps(px)
    assert pc.fetch_list_prices(env={}, opener=opener) is None  # no key: nothing is fetched


def test_catalog_without_the_model_is_an_error():
    with pytest.raises(CloudError):
        pc.prices_from_catalog({"items": [{"name": "other/model"}]}, "meta-models/x")


def test_cli_defaults_to_per_call_and_writes_a_sidecar_the_page_shows(made, tmp_path, capsys,
                                                                      monkeypatch):
    monkeypatch.delenv("BASETEN_METRICS_KEY", raising=False)
    monkeypatch.delenv("BASETEN_API_KEY", raising=False)
    path, r, _, _ = made
    for f in Path(path).parent.glob(path.stem + "*"):
        (tmp_path / f.name).write_bytes(f.read_bytes())
    p = tmp_path / path.name
    assert cli.main(["cloud", "audit", str(p)]) == 0
    out = capsys.readouterr().out
    assert "not the billing meter" in out and "100.0% match" in out
    side = json.loads(p.with_name(f"{r['receipt_id']}.percall-audit.json").read_text())
    assert side["source"] == "per-call" and side["status"] == "match"
    assert cli.main(["cloud", "show", str(p)]) == 0
    assert "per-call usage audit" in capsys.readouterr().out
    rv, _, _ = cloud_cli._load(str(p))
    html = view.render(rv)
    assert "Per-call usage audit" in html and "not the billing meter" in html.lower()


def test_cli_meter_source_still_says_not_available_without_a_key(made, tmp_path, monkeypatch,
                                                                 capsys):
    monkeypatch.delenv("BASETEN_API_KEY", raising=False)
    path, _, _, _ = made
    (tmp_path / path.name).write_bytes(path.read_bytes())
    (tmp_path / (path.stem + ".calls.jsonl")).write_bytes(
        path.with_name(path.stem + ".calls.jsonl").read_bytes())
    assert cli.main(["cloud", "audit", str(tmp_path / path.name), "--source", "meter"]) == 3
    assert "NOT_AVAILABLE" in capsys.readouterr().out
