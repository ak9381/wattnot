"""The dashboard: expert grouping, the energy split, the server, and label honesty."""
from __future__ import annotations

import gzip
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from wattnot.dash import experts, gpu
from wattnot.dash.server import DEFAULT_BUNDLE, DEFAULT_CAPTURE, create_app

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web" / "dash"


@pytest.fixture(scope="module")
def cap() -> experts.Capture:
    return experts.Capture.load(DEFAULT_CAPTURE)


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(create_app())


def test_tiers_split_each_layer_25_40_35(cap):
    counts = [0] * (cap.L * cap.E)
    for t in cap.ticks:
        if t["arm"] == "off":
            for u, n in t["r"]:
                counts[u] += n
    assign = experts.tier_assignment(counts, cap.L, cap.E)
    for layer in range(cap.L):
        row = assign[layer * cap.E:(layer + 1) * cap.E]
        assert row.count(0) == round(cap.E * 0.25)
        assert row.count(0) + row.count(1) + row.count(2) == cap.E


def test_kmeans_is_deterministic_and_orders_clusters_by_load(cap):
    a = experts.kmeans_assignment(cap, k=4)
    assert a == experts.kmeans_assignment(cap, k=4)
    assert sorted(set(a)) == [0, 1, 2, 3]


def test_energy_split_parts_add_up_to_the_measured_total():
    counts = [5, 0, 3, 9, 1, 2]
    assign = [0, 0, 1, 1, 2, 2]
    out = experts.energy_split(120.0, counts, assign, 3)
    assert out["total_label"] == "MEASURED" and out["parts_label"] == "ESTIMATE"
    assert sum(out["parts_j"]) == pytest.approx(120.0)
    assert out["parts_j"][1] == pytest.approx(120.0 * 12 / 20)


def test_meta_uses_the_measured_h100_rate_and_says_so(cap):
    meta = experts.build_meta(cap, json.loads(DEFAULT_BUNDLE.read_text())["pricing"])
    assert meta["energy"]["label"] == "MEASURED"
    assert meta["energy"]["off"] > meta["energy"]["on"] > 0
    # adaptive top-k really routes to fewer experts in the capture
    assert meta["totals"]["on"]["active_per_token"] < meta["totals"]["off"]["active_per_token"]
    assert meta["meta"]["capture"] == "cpu-local"
    assert meta["meta"]["energy"] == "none"  # the CPU capture had no meter


def test_every_step_has_tagged_tokens_in_the_capture(cap):
    ids = {s["id"] for s in cap.meta["steps"]}
    seen = {t["step"] for t in cap.ticks}
    assert seen == ids and len(ids) == 24
    assert all(t["tt"] for t in cap.ticks[:50])


def test_replay_bundle_comes_from_the_real_files_and_is_labelled():
    b = json.loads(DEFAULT_BUNDLE.read_text())
    assert {s["kind"] for s in b["scenes"]} == {"flex", "sweep", "adaptive"}
    assert all(s["label"] == "MEASURED" for s in b["scenes"])
    assert b["pricing"]["label"] == "MEASURED"
    ad = next(s for s in b["scenes"] if s["kind"] == "adaptive")
    assert {r["arm"] for r in ad["runs"]} == {"off", "on"}
    assert b["receipts"] and all(r["label"] for r in b["receipts"])
    # flex notes are computed from each receipt, not a fixed sentence
    notes = {s["note"] for s in b["scenes"] if s["kind"] == "flex"}
    assert len(notes) == 3


def test_api_serves_scenes_meta_and_ticks(client):
    assert client.get("/api/dash/health").json()["read_only"] is True
    info = client.get("/api/dash/gpu").json()
    assert info["scenes"] and "samples" not in info["scenes"][0]
    sid = info["scenes"][0]["id"]
    assert client.get(f"/api/dash/gpu/scene/{sid}").json()["samples"]
    meta = client.get("/api/dash/moe/meta").json()
    assert set(meta["modes"]) == {"tier", "kmeans"}
    ticks = client.get("/api/dash/moe/ticks").json()
    assert ticks and {"arm", "step", "r", "tt"} <= set(ticks[0])
    assert client.get("/api/dash/gpu/scene/nope").status_code == 404


def test_sse_replay_streams_samples(client):
    sid = client.get("/api/dash/gpu").json()["scenes"][0]["id"]
    with client.stream("GET", f"/api/dash/gpu/scene/{sid}/events?speed=5000") as r:
        body = "".join(r.iter_text())
    assert body.count("event: sample") > 50 and "event: end" in body


def test_server_is_read_only(client):
    for path in ("/api/dash/gpu", "/api/dash/moe/meta"):
        assert client.post(path).status_code in (404, 405)
        assert client.put(path).status_code in (404, 405)
        assert client.delete(path).status_code in (404, 405)


def test_follow_mode_reads_a_file_that_is_still_being_written(tmp_path):
    lines = list(experts.iter_lines(DEFAULT_CAPTURE))
    head = [x for x in lines if x["type"] in ("meta", "vocab", "unit_tokens")]
    ticks = [x for x in lines if x["type"] == "tick"][:30]
    f = tmp_path / "live.jsonl"
    f.write_text("\n".join(json.dumps(x) for x in head + ticks[:10]) + "\n")
    c = TestClient(create_app(follow=f))
    assert c.get("/api/dash/health").json()["following"] is True
    assert len(c.get("/api/dash/moe/ticks").json()) == 10
    with f.open("a") as fh:  # a half-written last line must not break the reader
        fh.write("\n".join(json.dumps(x) for x in ticks[10:20]) + "\n{\"type\":\"tic")
    assert len(c.get("/api/dash/moe/ticks").json()) == 20


def test_results_folder_can_be_read_directly(tmp_path):
    src = ROOT / "data" / "dash" / "h100-replay.json"
    assert src.is_file()
    # no results folder at hand: building from an empty one gives an empty, valid bundle
    b = gpu.build_bundle(tmp_path)
    assert b["scenes"] == [] and b["approvals"] == []


def test_ui_shows_a_label_next_to_every_energy_claim():
    js = "".join((WEB / n).read_text() for n in ("app.js", "flow.js", "orchestra.js", "gpu.js"))
    for word in ("MEASURED", "ESTIMATE"):
        assert word in js
    # no per-number text badges: the number carries the label colour, a dot names it
    assert 'pill("MEASURED")' not in js and 'pill("ESTIMATE")' not in js
    # per-group energy bars sit next to an ESTIMATE dot, the window total next to MEASURED
    flow = (WEB / "flow.js").read_text()
    assert re.search(r'o-j.*?dot\("MEASURED"\)', flow, re.S)
    assert re.search(r'per group \$\{dot\("ESTIMATE"\)\}', flow)


def test_visible_cards_have_no_label_text_badges():
    """MEASURED/ESTIMATE/SIMULATED/MOCK/REPLAY never render as pill() text badges
    in the visible card modules: numbers carry the label colour, a dot names it."""
    for n in ("archpage.js", "product.js", "gpu.js", "heat.js", "wastereplay.js",
              "partners.js", "app.js", "flow.js"):
        text = (WEB / n).read_text()
        assert not re.search(r'pill\("(?:MEASURED|ESTIMATE|SIMULATED|MOCK|REPLAY|ADVICE|LIVE)"\)', text), n
    # the helper stays exported and callable (operator panel, tests, data)
    assert "export const pill" in (WEB / "theme.js").read_text()


def test_shared_replay_clock_wiring():
    """One small clock module; Architecture publishes, Power and Experts follow it.
    The live modes keep their own streams and ignore the clock."""
    clock = (WEB / "replayclock.js").read_text()
    assert "setPhase" in clock and "setProgress" in clock
    assert "subscribe" in clock
    page = (WEB / "archpage.js").read_text()
    assert "replayclock" in page  # the Architecture replay publishes play/pause/progress/phase
    gpu = (WEB / "gpu.js").read_text()
    assert "replayclock" in gpu  # the Power scene replay subscribes (seek/pause to match)
    assert "live" in gpu  # and its live run mode stays on its own events
    app = (WEB / "app.js").read_text()
    assert "replayclock" in app  # the Experts tick replay subscribes too
    assert "moe/events" in app  # the live follow mode is untouched


def test_public_files_carry_no_agent_names():
    names = ["cla" + "ude", "gr" + "ok", "gem" + "ini", "anthr" + "opic", "open" + "ai"]
    bad = re.compile("|".join(names) + r"|\.cursor(?:rules|/)|agents\.md", re.I)
    files = [*WEB.glob("*"), *(ROOT / "src" / "wattnot" / "dash").glob("*.py"),
             ROOT / "scripts" / "moe_capture.py", ROOT / "scripts" / "build_dash_replay.py"]
    for f in files:
        if f.is_file():
            assert not bad.search(f.read_text()), f


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_core_js_unit_tests():
    r = subprocess.run(["node", "--test", str(ROOT / "tests" / "js")],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]


def test_capture_file_is_valid_gzip_jsonl():
    with gzip.open(DEFAULT_CAPTURE, "rt") as fh:
        first = json.loads(fh.readline())
    assert first["type"] == "meta" and first["model"] == "granite-3b-a800m"
