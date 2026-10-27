"""Partner attribution: log parsers, the snapshot build, and the endpoint."""
from __future__ import annotations

import json
import re
from pathlib import Path

from fastapi.testclient import TestClient

from wattnot.dash import partners as P
from wattnot.dash.server import create_app

ROOT = Path(__file__).resolve().parents[1]
SNAP = ROOT / "data" / "dash" / "partners.json"

META_TEXT = """# log

| Time | Via | Task | Calls | Input tok | Cached tok | Output tok | Cost | Ledger total |
|---|---|---|---|---|---|---|---|---|
| t | muse-code | job-a | 2 | 1,000 | 500 | 10 | $0.01 | $0.01 |
| t | muse-code | job-b | 1 | ~2,000 | 0 | ~5 | ~$0.02 (ESTIMATE) | |
|  |  | some prose about the jobs, no numbers | | | | | | |
"""

BASETEN_TEXT = """# log

| Time | Task | Model | Calls | Input tok | Cached tok | Output tok | Cost |
|---|---|---|---|---|---|---|---|
| t | check | m | 1 | 152 | 128 | 32 | $0.0002 |
"""


def test_meta_parser_skips_prose_and_flags_estimates():
    out = P.parse_meta_log(META_TEXT)
    assert out == {"calls": 3, "usd": 0.03, "rows": 2, "label": "ESTIMATE"}


def test_baseten_parser():
    out = P.parse_baseten_log(BASETEN_TEXT)
    assert out == {"calls": 1, "usd": 0.0002, "rows": 1, "label": "MEASURED"}


def test_e2e_cost_given_and_computed():
    run = {"run": "RUN1", "hours": 2.7, "rate_inr_per_h": 379.05, "gst_pct": 18,
           "cost_inr": 1215, "label": "ESTIMATE", "source": "s"}
    assert P.e2e_cost([run]) == {"total_inr": 1215.0, "label": "ESTIMATE"}
    calc = {"run": "R", "hours": 1.0, "rate_inr_per_h": 100.0, "gst_pct": 10,
            "cost_inr": None, "label": "MEASURED", "source": "s"}
    assert P.e2e_cost([calc]) == {"total_inr": 110.0, "label": "MEASURED"}
    assert P.e2e_cost([calc, run])["label"] == "ESTIMATE"


def test_build_with_and_without_inputs():
    full = P.build(meta_text=META_TEXT, baseten_text=BASETEN_TEXT,
                   e2e_runs=[{"run": "R", "hours": 1.0, "rate_inr_per_h": 100.0,
                              "gst_pct": 0, "cost_inr": 50, "label": "ESTIMATE",
                              "source": "s"}])
    assert full["format"] == "wattnot.partners/1"
    assert [p["id"] for p in full["partners"]] == list(P.IDS)
    by = {p["id"]: p for p in full["partners"]}
    assert by["meta"]["calls"] == 3 and by["meta"]["cost"]["unit"] == "USD"
    assert by["meta"]["status"] == "used" and "note" not in by["meta"]
    assert by["baseten"]["calls"] == 1
    assert by["e2e"]["cost"] == {"value": 50.0, "unit": "INR", "label": "ESTIMATE"}
    for pid in ("fireworks", "modal"):
        assert by[pid]["status"] == "not_used" and by[pid]["what"] == "not used yet"
        assert by[pid]["calls"] is None and by[pid]["cost"]["value"] is None
    empty = P.build()
    for pid in ("meta", "baseten", "e2e"):
        assert empty["partners"][list(P.IDS).index(pid)]["note"] == "ledger not provided"
        assert empty["partners"][list(P.IDS).index(pid)]["status"] == "used"
        assert empty["partners"][list(P.IDS).index(pid)]["cost"]["value"] is None
    assert "/workspace" not in json.dumps(full)


def test_snapshot_in_the_repo_validates():
    snap = json.loads(SNAP.read_text())
    assert snap["format"] == "wattnot.partners/1" and snap["generated_at"]
    assert [p["id"] for p in snap["partners"]] == list(P.IDS)
    by = {p["id"]: p for p in snap["partners"]}
    assert by["meta"]["calls"] == 464 and by["meta"]["cost"]["value"] == 10.69
    assert by["meta"]["cost"] == {"value": 10.69, "unit": "USD", "label": "ESTIMATE"}
    assert by["baseten"]["calls"] == 1
    assert by["baseten"]["cost"] == {"value": 0.0002, "unit": "USD", "label": "MEASURED"}
    assert by["e2e"]["cost"] == {"value": 1215.0, "unit": "INR", "label": "ESTIMATE"}
    assert by["nvidia"]["cost"] == {"value": None, "unit": None, "label": None}
    blob = SNAP.read_text()
    assert "/workspace" not in blob and "/tmp" not in blob
    for pat in (r"sk-[A-Za-z0-9]{16,}", r"Bearer\s+\S{12,}",
                r"BEGIN [A-Z ]*PRIVATE KEY", r"api[_-]?key\"\s*:\s*\"\S{12,}",
                r"ssh-(?:rsa|ed25519) AAAA"):
        assert not re.search(pat, blob), pat


def test_partners_endpoint_200_and_404(tmp_path):
    c = TestClient(create_app())
    r = c.get("/api/dash/partners")
    assert r.status_code == 200 and r.json()["format"] == "wattnot.partners/1"
    assert [p["id"] for p in r.json()["partners"]] == list(P.IDS)
    c = TestClient(create_app(partners=tmp_path / "none.json"))
    r = c.get("/api/dash/partners")
    assert r.status_code == 404 and "build_partners" in r.json()["detail"]
    c = TestClient(create_app(replay_only=True))
    assert c.get("/api/dash/partners").status_code == 200
