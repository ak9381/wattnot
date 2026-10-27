"""Receipt, meter audit, cache probes, the report page, the CLI and the API view."""

import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from wattnot import cli
from wattnot.api import app as api_app
from wattnot.cloud import audit as au
from wattnot.cloud import cache as ch
from wattnot.cloud import receipt as rc
from wattnot.cloud import run as rn
from wattnot.cloud.pricing import Rates
from wattnot.cloud.transport import FixtureTransport, Reply
from wattnot.receipt.sign import generate_test_key
from wattnot.report import cloud as view

ROOT = Path(__file__).resolve().parents[1]
NOW = 1_790_000_000.0


def demo(tmp_path, **kw):
    ft = FixtureTransport.load(rn.DEMO_FIXTURE)
    return rn.run(rn.Options(**kw), ft, "replay-synthetic", out_dir=tmp_path, now=NOW)


def rows_of(path):
    return [json.loads(x) for x in path.with_suffix(".calls.jsonl").read_text().splitlines()]


def test_synthetic_receipt_is_simulated_everywhere_and_passes_the_lint(tmp_path):
    path, r = demo(tmp_path)
    assert r["label"] == "SIMULATED" and r["mode"] == "replay-synthetic"
    assert "MEASURED" not in json.dumps(r)
    assert rc.check(r) == []
    assert any("synthetic" in n for n in r["unverified"])
    assert r["energy"]["label"] == "ESTIMATE"


def test_receipt_has_baseline_and_totals_add_up(tmp_path):
    _, r = demo(tmp_path)
    t = r["totals"]
    assert t["would_have_cost_xhigh_usd"] > t["tuned_usd"] > 0
    assert t["saving_usd"] == pytest.approx(t["would_have_cost_xhigh_usd"] - t["tuned_usd"],
                                            abs=1e-6)
    assert sum(c["measured"]["usd"] for c in r["classes"]) == pytest.approx(
        t["tuned_usd"], abs=1e-6)
    got = {c["class"]: c["chosen_effort"] for c in r["classes"]}
    assert got["logic"] == "high" and got["arith"] == "medium" and got["extract"] == "low"
    assert t["experiment_usd"] > t["tuned_usd"]  # the search itself costs more than one pass
    assert t["payback_runs"] and t["payback_runs"] > 1


def test_calls_file_is_hashed_and_holds_no_text(tmp_path):
    path, r = demo(tmp_path)
    rows = rows_of(path)
    assert rc.calls_digest(rows) == r["calls"]["sha256"] and len(rows) == r["calls"]["n"]
    text = path.with_suffix(".calls.jsonl").read_text()
    assert "ANSWER" not in text and "reasoning_content" not in text
    assert any(x["effort"] == "xhigh" for x in rows) and rows[0]["tag"] == "propose"


def test_hash_chain_links_to_the_previous_receipt_and_detects_edits(tmp_path):
    p1, r1 = demo(tmp_path)
    _, r2 = rn.run(rn.Options(), FixtureTransport.load(rn.DEMO_FIXTURE), "replay-synthetic",
                   out_dir=tmp_path, now=NOW + 5)
    assert r1["integrity"]["prev_sha256"] is None
    assert r2["integrity"]["prev_sha256"] == r1["integrity"]["sha256"]
    assert rc.verify_hash(r1)[0]
    r1["totals"]["saving_pct"] = 99.0
    assert not rc.verify_hash(r1)[0]


def test_offline_signature_does_not_change_the_hash(tmp_path):
    key = generate_test_key(tmp_path / "k.pem")
    _, r = rn.run(rn.Options(), FixtureTransport.load(rn.DEMO_FIXTURE), "replay-synthetic",
                  out_dir=tmp_path / "o", now=NOW, sign_key=key)
    assert r["integrity"]["signer"] and rc.verify_hash(r)[0]


def test_lint_rejects_text_and_banned_wording(tmp_path):
    _, r = demo(tmp_path)
    bad = json.loads(json.dumps(r))
    bad["note"] = "x" * 400
    assert rc.check(bad)
    bad = json.loads(json.dumps(r))
    bad["planner"]["note"] = "this makes the run carbon neutral"
    assert any("banned" in p for p in rc.check(bad))


def test_tiny_budget_still_makes_a_valid_receipt(tmp_path):
    _, r = demo(tmp_path, budget_usd=0.0001)
    assert r["stopped"] and rc.check(r) == []
    assert all(not c["kept"] for c in r["classes"])
    assert r["totals"]["saving_usd"] == 0


def test_efforts_flag_limits_the_levels_tried(tmp_path):
    _, r = demo(tmp_path, efforts=["high"], classes=["arith"])
    assert [t["effort"] for t in r["classes"][0]["tried"]] == ["high"]


def test_key_never_lands_in_a_receipt(tmp_path):
    _, r = rn.run(rn.Options(), FixtureTransport.load(rn.DEMO_FIXTURE), "replay-synthetic",
                  out_dir=tmp_path, now=NOW, api_key_prefix="abc123")
    text = json.dumps(r)
    assert "abc123" not in text
    assert len(r["api_key_prefix_sha256"]) == 16


# ---- audit ----------------------------------------------------------------------------------

def test_audit_matches_within_tolerance_and_flags_a_gap(tmp_path):
    path, r = demo(tmp_path)
    meter = json.loads(rn.DEMO_METER.read_text())["items"]
    ok = au.audit(r, rows_of(path), meter)
    assert ok["status"] == "match" and ok["worst_gap_pct"] < 1
    meter[0]["results"][0]["output_tokens"] = int(meter[0]["results"][0]["output_tokens"] * 1.2)
    bad = au.audit(r, rows_of(path), meter)
    assert bad["status"] == "mismatch" and not [x for x in bad["rows"] if x["field"] ==
                                                "output_tokens"][0]["ok"]


def test_audit_ignores_other_models_and_says_unavailable_without_data(tmp_path):
    path, r = demo(tmp_path)
    meter = json.loads(rn.DEMO_METER.read_text())["items"]
    meter[0]["results"].append({"model": "other/model", "request_count": 5,
                                "input_tokens": 9, "output_tokens": 9})
    assert au.audit(r, rows_of(path), meter)["status"] == "match"
    na = au.audit(r, rows_of(path), None, why_unavailable="no key")
    assert na["status"] == "unavailable" and na["label"] == "NOT_AVAILABLE"


def test_usage_endpoint_url_filters_by_key_prefix(monkeypatch):
    seen = {}

    class R:
        def __init__(self, d): self.d = d
        def read(self): return json.dumps(self.d).encode()
        def __enter__(self): return self
        def __exit__(self, *a): return False

    pages = [{"items": [{"results": []}], "pagination": {"has_more": True, "cursor": "c2"}},
             {"items": [{"results": []}], "pagination": {"has_more": False}}]

    def opener(req, timeout):
        seen.setdefault("urls", []).append(req.full_url)
        seen["auth"] = req.headers["Authorization"]
        return R(pages.pop(0))

    got = au.fetch_usage("2026-10-27T06:00:00Z", "2026-10-27T06:05:00Z",
                         env={"BASETEN_API_KEY": "abc123.SECRET"}, opener=opener)
    assert len(got) == 2 and "api_keys=abc123" in seen["urls"][0]
    assert "SECRET" not in seen["urls"][0] and "cursor=c2" in seen["urls"][1]
    assert seen["auth"] == "Bearer abc123.SECRET"


# ---- cache ----------------------------------------------------------------------------------

class PrefixCache:
    """A fake server that caches by exact prompt prefix, to test the layout logic itself."""

    def __init__(self):
        self.seen: list[str] = []

    def __call__(self, req):
        text = "".join(m["content"] for m in req.body["messages"])
        best = max((len(_common(text, s)) for s in self.seen), default=0)
        self.seen.append(text)
        prompt = len(text) // 4
        cached = min(prompt, (best // 4) // 16 * 16)
        return Reply(200, {"choices": [{"message": {"content": "Section 3."},
                                        "finish_reason": "stop"}],
                           "usage": {"prompt_tokens": prompt, "completion_tokens": 5,
                                     "prompt_tokens_details": {"cached_tokens": cached}}},
                     1.0, {}, 0, 1.0)


def _common(a, b):
    n = 0
    for x, y in zip(a, b, strict=False):
        if x != y:
            break
        n += 1
    return a[:n]


def test_stable_first_layout_gets_cached_and_volatile_first_does_not():
    res = ch.cache_ab(PrefixCache(), Rates(), n=6)
    arms = {a["arm"]: a for a in res["arms"]}
    assert arms["volatile-first"]["cached_share"] < 0.05
    assert arms["stable-first"]["cached_share_after_first"] > 0.8
    assert arms["stable-first"]["usd"] < arms["volatile-first"]["usd"]


def test_probe_reads_each_wait_from_its_own_warm_copy():
    waits, slept = (5, 20), []
    res = ch.probe_half_life(PrefixCache(), Rates(), waits=waits, sleep=slept.append)
    assert [p["wait_s"] for p in res["curve"]] == [5, 20]
    assert all(p["cached_share"] > 0.8 for p in res["curve"])  # fake server never forgets
    assert slept  # it did wait between reads


# ---- report, API, CLI -----------------------------------------------------------------------

def test_html_report_is_self_contained_and_labelled(tmp_path):
    _, r = demo(tmp_path)
    page = view.render(r)
    assert "<script" not in page and "http://" not in page.replace("http://www.w3.org", "")
    assert "SIMULATED" in page and "ESTIMATE" in page and "would have cost" in page
    assert "Not verified" in page and "MEASURED" in page  # MEASURED only in the legend text
    lines = view.summary_lines(r)
    assert any("pays back" in x for x in lines) and "".join(lines).count("SIMULATED") >= 4


def test_api_lists_and_serves_cloud_receipts(tmp_path):
    _, r = demo(tmp_path / "cloud-receipts")
    c = TestClient(api_app.create_app(root=tmp_path))
    rows = c.get("/api/cloud/receipts").json()
    assert rows[0]["receipt_id"] == r["receipt_id"] and rows[0]["label"] == "SIMULATED"
    assert c.get(f"/api/cloud/receipts/{r['receipt_id']}").json()["integrity"]["sha256"]
    html = c.get(f"/api/cloud/receipts/{r['receipt_id']}?format=html")
    assert html.status_code == 200 and "Cloud mode receipt" in html.text
    assert c.get("/api/cloud/receipts/nope").status_code == 404
    assert c.get("/api/cloud/receipts/..%2Fx").status_code == 404
    assert c.get(f"/api/cloud/receipts/{r['receipt_id']}?format=xml").status_code == 400


def test_cli_demo_end_to_end(tmp_path, capsys):
    assert cli.main(["cloud", "demo", "--out", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "SIMULATED" in out and "match" in out
    files = {p.suffix for p in tmp_path.iterdir()}
    assert {".json", ".html", ".jsonl"} <= files
    rid = next(p for p in tmp_path.glob("cloud_*.json") if ".audit" not in p.name)
    assert cli.main(["cloud", "show", str(rid)]) == 0
    assert "meter audit: match" in capsys.readouterr().out
    rid.write_text(rid.read_text().replace('"saving_pct": ', '"saving_pct": 9', 1))
    assert cli.main(["cloud", "show", str(rid)]) == 2  # a tampered receipt is refused


def test_cli_live_without_key_fails_clearly(monkeypatch, capsys, tmp_path):
    monkeypatch.delenv("BASETEN_API_KEY", raising=False)
    assert cli.main(["cloud", "tune", "--live", "--out", str(tmp_path)]) == 2
    err = capsys.readouterr().err
    assert "BASETEN_API_KEY is not set" in err
    assert cli.main(["cloud", "tune", "--out", str(tmp_path)]) == 2  # no source picked


def test_cli_cache_and_probe_demo_are_simulated(tmp_path, capsys):
    assert cli.main(["cloud", "cache-ab", "--demo", "--out", str(tmp_path)]) == 0
    assert cli.main(["cloud", "probe", "--demo", "--out", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert out.count("[SIMULATED]") == 2 and "[MEASURED]" not in out


def test_cli_audit_without_key_says_not_available(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("BASETEN_API_KEY", raising=False)
    path, _ = demo(tmp_path)
    assert cli.main(["cloud", "audit", str(path)]) == 3
    assert "NOT_AVAILABLE" in capsys.readouterr().out


# ---- repo hygiene ---------------------------------------------------------------------------

CLOUD_FILES = [*(ROOT / "src/wattnot/cloud").glob("*.py"), ROOT / "src/wattnot/report/cloud.py",
               ROOT / "docs/runbooks/BASETEN.md", ROOT / "data/cloud/shared-prefix.txt",
               ROOT / "workloads/cloud-effort-v1.jsonl", ROOT / "scripts/gen_cloud_fixture.py"]


def test_public_files_hold_no_secret_looking_values():
    pat = re.compile(r"\bsk-[A-Za-z0-9]{10,}\b|BASETEN_API_KEY=[A-Za-z0-9]{8,}")
    for f in CLOUD_FILES:
        if f.exists():
            assert not pat.search(f.read_text()), f
            assert "carbon neutral" not in f.read_text().lower(), f


def test_no_file_reads_the_key_from_anywhere_but_the_environment():
    src = "\n".join(f.read_text() for f in (ROOT / "src/wattnot/cloud").glob("*.py"))
    assert src.count("os.environ") == 1  # one place reads the environment
    assert "--api-key" not in src and "--key " not in src
    assert "BASETEN_API_KEY" in (ROOT / "src/wattnot/cloud/__init__.py").read_text()


def test_committed_demo_fixture_is_marked_synthetic():
    doc = json.loads(rn.DEMO_FIXTURE.read_text())
    assert doc["source"] == "synthetic" and "no API call" in doc["note"]


def test_recorded_fixture_replays_as_measured_with_the_unconfirmed_price_note(tmp_path):
    from wattnot.cloud.transport import Recorder

    rec = Recorder(FixtureTransport.load(rn.DEMO_FIXTURE))
    rn.run(rn.Options(), rec, "live", out_dir=tmp_path / "live", now=NOW)
    fx = rec.save(tmp_path / "rec.json")
    ft, mode, _ = rn.open_transport(live=False, fixture=fx)
    assert mode == "replay-recorded"
    _, r = rn.run(rn.Options(), ft, mode, out_dir=tmp_path / "again", now=NOW)
    assert r["label"] == "MEASURED" and r["totals"]["label"] == "MEASURED"
    assert any("invoice" in n for n in r["unverified"])
    assert rc.check(r) == []


def test_usage_403_is_reported_with_a_hint_and_never_the_key():
    import io
    import urllib.error

    import pytest

    from wattnot.cloud import audit as au
    from wattnot.cloud.transport import CloudError

    seen = {}

    def opener(req, timeout=0):
        seen["auth"] = req.get_header("Authorization")
        body = b'{"code": "PERMISSION_DENIED", "message": "Authorization error"}'
        raise urllib.error.HTTPError(req.full_url, 403, "no", {}, io.BytesIO(body))

    env = {"BASETEN_API_KEY": "abc.SECRETSECRET"}
    with pytest.raises(CloudError) as ei:
        au.fetch_usage("2026-10-27T00:00:00Z", "2026-10-27T01:00:00Z", env=env, opener=opener)
    msg = str(ei.value)
    assert "403" in msg and "PERMISSION_DENIED" in msg and "BASETEN_METRICS_KEY" in msg
    assert "SECRETSECRET" not in msg
    # with a metrics key set, that key authenticates, but rows are still filtered to the prefix
    env["BASETEN_METRICS_KEY"] = "met.OTHERSECRET"
    with pytest.raises(CloudError):
        au.fetch_usage("2026-10-27T00:00:00Z", "2026-10-27T01:00:00Z", env=env, opener=opener)
    assert seen["auth"] == "Bearer met.OTHERSECRET"


def test_usage_403_says_when_the_metrics_key_is_the_inference_key():
    import io
    import urllib.error

    import pytest

    from wattnot.cloud import audit as au
    from wattnot.cloud.transport import CloudError

    def opener(req, timeout=0):
        raise urllib.error.HTTPError(req.full_url, 403, "no", {}, io.BytesIO(b"{}"))

    env = {"BASETEN_API_KEY": "abc.SECRETSECRET", "BASETEN_METRICS_KEY": "abc.SECRETSECRET"}
    with pytest.raises(CloudError, match="same key as the inference key") as ei:
        au.fetch_usage("2026-10-27T00:00:00Z", "2026-10-27T01:00:00Z", env=env, opener=opener)
    assert "SECRETSECRET" not in str(ei.value)
