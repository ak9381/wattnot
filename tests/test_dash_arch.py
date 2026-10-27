"""Architecture map, story bundle, hosted-mode gate and the Meta health check.

The map is only worth showing if it is true. These tests open every file and symbol the spec
names, check that a number with no value never carries a label, and that the bundle holds no
secret. The server tests use stubs: nothing here calls a real API.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from wattnot.dash import arch_spec as spec
from wattnot.dash import server
from wattnot.dash.server import create_app, meta_check

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "src" / "wattnot"
STORY = ROOT / "data" / "dash" / "story.json"


@pytest.fixture(scope="module")
def bundle() -> dict:
    return json.loads(STORY.read_text())


def _defined(text: str, name: str) -> bool:
    return re.search(rf"^\s*(?:async\s+)?(?:def|class)\s+{re.escape(name)}\b|^\s*{re.escape(name)}\s*[:=]", text, re.M) is not None


def test_every_node_and_sub_step_names_real_code():
    missing = []
    for n in spec.NODES:
        f = PKG / n["code"]
        if not f.is_file():
            missing.append(n["code"])
            continue
        for s in n["subs"]:
            path, _, sym = s["code"].partition("::")
            sf = PKG / path
            if not sf.is_file():
                missing.append(s["code"])
            elif sym and not all(_defined(sf.read_text(), part) for part in sym.split(".")):
                missing.append(s["code"])
    assert not missing, missing


def test_nodes_edges_and_events_are_consistent():
    ids = {n["id"] for n in spec.NODES}
    assert len(ids) == len(spec.NODES)
    for e in spec.EDGES:
        assert e[0] in ids and e[1] in ids and e[2] in spec.PARTNERS
    for n in spec.NODES:
        assert set(n["partners"]) <= set(spec.PARTNERS)
        assert len({s["id"] for s in n["subs"]}) == len(n["subs"])
    subs = {(n["id"], s["id"]) for n in spec.NODES for s in n["subs"]}
    for ev, target in spec.EVENT_MAP.items():
        assert tuple(target) in subs, ev


def test_bundle_matches_spec(bundle):
    assert bundle["schema"] == "wattnot.dash.story/1"
    assert [n["id"] for n in bundle["nodes"]] == [n["id"] for n in spec.NODES]
    assert bundle["event_map"] == {k: list(v) for k, v in spec.EVENT_MAP.items()}
    for n in bundle["nodes"]:
        assert n["status"] in {"code", "stub", "roadmap", "not-run"}


def test_label_honesty(bundle):
    """A missing value is shown as a dash with no label; a label is always one of the five."""
    stats = [s for n in bundle["nodes"] for s in n["stats"]] + [s for m in bundle["meta"] for s in m["stats"]]
    assert stats
    for s in stats:
        if s["v"] is None:
            assert s["tag"] is None, s
        elif s["tag"] is None:
            assert isinstance(s["v"], str), s  # a configured setting such as a step size, not a measurement
            assert s["src"], s
        else:
            assert s["tag"] in spec.LABELS, s
            assert s["src"], s
    for lever in bundle["story"]["levers"]:
        assert lever["id"] in {"cap", "topk", "hosted"}


def test_cap_lever_does_not_claim_quality_passed(bundle):
    cap = next(x for x in bundle["story"]["levers"] if x["id"] == "cap")
    assert cap["quality"]["state"] == "pending"


def test_water_is_an_estimate_with_a_cited_factor(bundle):
    w = bundle["story"]["water"]
    assert w["factor_l_per_kwh"] > 0 and w["source"] and w["url"].startswith("https://")
    assert "kWh" in w["formula"]


def test_bundle_has_no_secrets(bundle):
    blob = json.dumps(bundle)
    for pat in (r"sk-[A-Za-z0-9]{16,}", r"Bearer\s+\S{12,}", r"BEGIN [A-Z ]*PRIVATE KEY", r"api[_-]?key\"\s*:\s*\"\S{12,}",
                r"ssh-(?:rsa|ed25519) AAAA"):
        assert not re.search(pat, blob), pat


def test_arch_cards_collapse_with_expand_all():
    js = (ROOT / "web" / "dash" / "arch.js").read_text()
    page = (ROOT / "web" / "dash" / "archpage.js").read_text()
    css = (ROOT / "web" / "dash" / "story.css").read_text()
    assert "aria-expanded" in js and "aria-controls" in js
    assert "expandAll" in js and "collapseAll" in js
    assert "Expand all" in page and "Collapse all" in page
    assert "backdrop-filter" in css and "-webkit-backdrop-filter" in css
    assert re.search(r"#arch[^{]*\{[^}]*backdrop-filter", css)
    assert "aglass" in js


def test_repo_names_no_agent_or_model_vendor():
    names = ["cla" + "ude", "anthr" + "opic", "open" + "ai", "chat" + "gpt", "gem" + "ini", "copi" + "lot", "gr" + "ok"]
    banned = re.compile("|".join(names), re.I)
    roots = [ROOT / "web" / "dash", ROOT / "src" / "wattnot" / "dash", ROOT / "docs" / "dashboard.md", STORY]
    hits = []
    for r in roots:
        for f in ([r] if r.is_file() else r.rglob("*")):
            if f.is_file() and f.suffix in {".js", ".css", ".html", ".py", ".md", ".json"} and "vendor" not in f.parts:
                if banned.search(f.read_text(errors="ignore").replace("OpenAIClient", "")):
                    hits.append(str(f.relative_to(ROOT)))
    assert not hits, hits


def test_arch_edges_march_with_static_dashed_idle():
    css = (ROOT / "web" / "dash" / "story.css").read_text()
    assert "@keyframes march" in css and "stroke-dashoffset" in css
    base = re.search(r"\.av-edges \.e\{([^}]*)\}", css).group(1)
    assert "stroke-dasharray" in base  # idle edges are dashed and static
    assert "animation" not in base
    flow = re.search(r"\.av-edges \.e\.flow\{([^}]*)\}", css).group(1)
    assert "march" in flow and "stroke-dasharray" in flow  # inflow edges animate the dash
    rm = css.split("prefers-reduced-motion", 1)[1]
    assert "animation:none" in rm.replace(" ", "")
    assert "stroke-dasharray:none" not in rm  # highlight colour kept, motion off


def test_arch_meter_strip_and_card_bars(bundle):
    page = (ROOT / "web" / "dash" / "archpage.js").read_text()
    js = (ROOT / "web" / "dash" / "arch.js").read_text()
    css = (ROOT / "web" / "dash" / "story.css").read_text()
    assert "mmeter" in page and "meterFor" in page and "meterStage" in page
    assert "WUE" in page and "per 1M requests" in page
    assert '"MEASURED"' in page and '"ESTIMATE"' in page and 'pill("REPLAY")' in page
    assert "mbar" in js and "barPct" in js and ".mbar" in css and ".mmeter" in css
    assert ":not(.open) .d" in css and ":not(.open) .pw" in css  # collapsed: no description
    assert "aplay" in page and "areset" in page and "afil" in page and "apanel" in page
    # the strip's inputs really exist in the bundle it reads
    assert bundle["story"]["water"]["factor_l_per_kwh"] > 0
    assert bundle["story"]["price"]["usd_per_kwh"] > 0
    levers = {x["id"]: x for x in bundle["story"]["levers"]}
    assert levers["cap"]["wh_before"] and levers["cap"]["wh_after"]
    assert levers["hosted"]["usd_before"] and levers["hosted"]["usd_after"]
    assert set(bundle["replays"]) >= {"h100", "hosted"}
    tele = next(n for n in bundle["nodes"] if n["id"] == "telemetry")
    assert next(s for s in tele["stats"] if s["k"] == "probe_w")["v"] > 0


# ---- server --------------------------------------------------------------------------------

def test_story_and_health_endpoints():
    c = TestClient(create_app())
    h = c.get("/api/dash/health").json()
    assert h["story"] is True and h["read_only"] is True and h["auth_required"] is False
    assert c.get("/api/dash/story").json()["schema"] == "wattnot.dash.story/1"


def test_story_missing_is_a_clean_404(tmp_path):
    c = TestClient(create_app(story=tmp_path / "none.json"))
    r = c.get("/api/dash/story")
    assert r.status_code == 404 and "build_dash_arch" in r.json()["detail"]
    assert c.get("/api/dash/health").json()["story"] is False


def test_token_gate_login_logout():
    c = TestClient(create_app(token="s3cret"), follow_redirects=False)
    assert c.get("/api/dash/story").status_code == 401
    assert c.get("/api/dash/health").status_code == 200
    r = c.get("/")
    assert r.status_code == 303 and r.headers["location"] == "/login"
    assert "access token" in c.get("/login").text
    bad = c.post("/login", content="token=nope", headers={"content-type": "application/x-www-form-urlencoded"})
    assert bad.status_code == 303 and "bad=1" in bad.headers["location"]
    assert "did not match" in c.get("/login?bad=1").text
    good = c.post("/login", content="token=s3cret", headers={"content-type": "application/x-www-form-urlencoded"})
    assert good.status_code == 303 and "wn_auth" in good.headers["set-cookie"]
    c.cookies.set("wn_auth", server._mac("s3cret"))
    assert c.get("/api/dash/story").status_code == 200
    assert c.get("/logout").status_code == 303
    assert c.get("/api/dash/story", headers={"authorization": "Bearer s3cret"}).status_code == 200


def test_token_gate_rejects_wrong_bearer():
    c = TestClient(create_app(token="s3cret"), follow_redirects=False)
    assert c.get("/api/dash/story", headers={"authorization": "Bearer wrong"}).status_code == 401
    assert c.get("/api/dash/story", headers={"authorization": "Bearer s3cret"}).status_code == 200


def test_meta_health_uses_the_probe_and_never_500s():
    ok = {"ok": True, "state": "ok", "steps": [], "advice": "Connected.", "spends_money": False}
    assert TestClient(create_app(meta_probe=lambda: ok)).get("/api/dash/meta/health").json()["state"] == "ok"

    def boom():
        raise RuntimeError("x")
    r = TestClient(create_app(meta_probe=boom)).get("/api/dash/meta/health")
    assert r.status_code == 200 and r.json()["state"] == "error" and r.json()["spends_money"] is False


@pytest.fixture
def no_net(monkeypatch):
    monkeypatch.setattr(server.socket, "getaddrinfo", lambda *a, **k: [("x",)])

    class Conn:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False
    monkeypatch.setattr(server.socket, "create_connection", lambda *a, **k: Conn())

    class Ctx:
        def wrap_socket(self, s, server_hostname=None):
            return Conn()
    monkeypatch.setattr(server.ssl, "create_default_context", lambda: Ctx())


@pytest.mark.parametrize("key,code,state", [("k", 200, "ok"), ("k", 401, "key_rejected"), (None, 401, "key_missing"),
                                              (None, 200, "key_missing"), ("k", 503, "degraded")])
def test_meta_check_states(monkeypatch, no_net, key, code, state):
    monkeypatch.setattr("wattnot.meta.client.load_key", lambda *a, **k: key)
    seen = {}

    def opener(req, timeout):
        seen["auth"] = req.get_header("Authorization")
        return code
    out = meta_check(opener=opener)
    assert out["state"] == state and out["spends_money"] is False
    assert seen["auth"] == (f"Bearer {key}" if key else None)
    assert out["steps"][0]["ok"] is bool(key)


def test_meta_check_unreachable_and_blocked(monkeypatch):
    monkeypatch.setattr("wattnot.meta.client.load_key", lambda *a, **k: "k")

    def nodns(*a, **k):
        raise OSError("no dns")
    monkeypatch.setattr(server.socket, "getaddrinfo", nodns)
    assert meta_check()["state"] == "unreachable"
    monkeypatch.setattr(server.socket, "getaddrinfo", lambda *a, **k: [("x",)])

    def refuse(*a, **k):
        raise ConnectionRefusedError()
    monkeypatch.setattr(server.socket, "create_connection", refuse)
    out = meta_check()
    assert out["state"] == "blocked" and "replay" in out["advice"].lower()


def test_meta_check_never_returns_the_key(monkeypatch, no_net):
    monkeypatch.setattr("wattnot.meta.client.load_key", lambda *a, **k: "KEYVALUE-1234567890")
    out = meta_check(opener=lambda req, t: 200)
    assert "KEYVALUE" not in json.dumps(out)


def test_live_endpoint_404_without_events_file_and_streams_with_one(tmp_path):
    assert TestClient(create_app()).get("/api/dash/arch/live").status_code == 404


def test_hosted_replay_only_makes_no_meta_call_and_says_replay(tmp_path, monkeypatch):
    saved = tmp_path / "run.jsonl"
    saved.write_text('{"schema_version":"wattnot.event/1","type":"run.started"}\n')

    def boom():
        raise AssertionError("the hosted replay must not probe Meta")

    c = TestClient(create_app(replay_only=True, events=saved, meta_probe=boom))
    h = c.get("/api/dash/health").json()
    assert h["mode"] == "replay" and h["replay_only"] is True
    assert h["meta_calls"] is False and h["gpu_attached"] is False and h["events"] is True
    m = c.get("/api/dash/meta/health").json()
    assert m["state"] == "replay_only" and m["called"] is False and m["spends_money"] is False
    # the plan endpoint is pure computation on the bundled recorded runs
    assert c.post("/api/dash/run/plan", json={"model": "glimmer"}).status_code == 200


def test_hosted_replay_flag_from_environment(monkeypatch):
    monkeypatch.setenv("WATTNOT_HOSTED_REPLAY", "1")
    assert TestClient(create_app()).get("/api/dash/health").json()["replay_only"] is True
    monkeypatch.delenv("WATTNOT_HOSTED_REPLAY")
    assert TestClient(create_app()).get("/api/dash/health").json()["replay_only"] is False


def test_room_module_is_served_for_run_events() -> None:
    """A room_scan payload is drawn with the room module's node, served read-only under /room."""
    c = TestClient(create_app())
    r = c.get("/room/node.js")
    assert r.status_code == 200 and "mountNode" in r.text
    assert c.get("/room/data/arsat/scan.json").status_code == 200


# ---- single Architecture page: the Experts and Power/Room tabs are merged in --
def test_nav_has_only_the_architecture_tab():
    """The top nav and the drawer list one tab: Architecture."""
    html = (ROOT / "web" / "dash" / "index.html").read_text()
    assert re.findall(r'data-tab="([^"]+)"', html) == ["arch"]
    assert 'id="dPower"' not in html and 'id="dExperts"' not in html
    assert 'id="dArch"' in html
    assert 'id="expertCtl"' not in html


def test_legacy_tab_urls_redirect_to_architecture():
    """?tab=experts, ?tab=power, ?tab=room and hash routes land on Architecture."""
    js = (ROOT / "web" / "dash" / "app.js").read_text()
    assert "LEGACY_TABS" in js and "location.hash" in js
    for old in ("experts", "power", "room"):
        assert re.search(rf"""['"]{old}['"]""", js), old
    # legacy ids resolve to the arch page with the matching drill-down, never a tab
    assert js.count('openDrawer("arch"') >= 2
    assert 'q.set("expert", "1")' in js and 'q.set("room", "sam")' in js


def test_arch_expert_toggle_and_room_drilldown():
    """An Expert toggle in the Architecture toolbar; the rack map opens the racks."""
    page = (ROOT / "web" / "dash" / "archpage.js").read_text()
    assert 'id="aexpert"' in page and "aria-pressed" in page
    assert "/api/dash/moe/meta" in page and "core.js" in page  # expert details reuse the expert content
    assert "sam" in page and ("heatSvg" in page or "mountRoomScan" in page)  # rack drill-down reuses room rendering


def test_arch_deep_links_stay_inside_architecture():
    """No card links out to a removed Power/Experts tab anymore."""
    js = (ROOT / "web" / "dash" / "arch.js").read_text()
    assert "Open in Power" not in js and "Open in Experts" not in js
    m = re.search(r"export const DEEP = \{([^}]*)\}", js)
    # drill-down keys stay inside Architecture (expert toggle, rack map), never a removed tab
    assert m and set(re.findall(r':\s*"([^"]+)"', m.group(1))) <= {"experts", "room"}
