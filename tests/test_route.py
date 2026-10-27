"""Router: gate first, joules next, carbon only as a tie-break. Replay costs nothing."""

from __future__ import annotations

import json
import urllib.request
from pathlib import Path

import pytest

from wattnot.cli import main
from wattnot.cloud import run as cloud_run
from wattnot.receipt.canon import canonical_sha256
from wattnot.route import grid as G
from wattnot.route.replay import format_report, replay
from wattnot.route.router import Candidate, choose, class_candidates, expand_regions

NOW = 1_792_000_000.0
GRID = G.StaticGrid()


def cand(id, wh, *, passed=True, pass_rate=1.0, usd=0.01, region=None, note=""):
    return Candidate(id=id, backend="b", effort=id, usd_per_req=usd, wh_low=wh, wh_high=wh,
                     pass_rate=pass_rate, gate_passed=passed, gate_note=note, region=region)


@pytest.fixture(scope="module")
def cloud(tmp_path_factory):
    d = tmp_path_factory.mktemp("cloud")
    t, mode, _ = cloud_run.open_transport(live=False, fixture=cloud_run.DEMO_FIXTURE)
    path, _ = cloud_run.run(cloud_run.Options(), t, mode, out_dir=d, now=NOW)
    return json.loads(path.read_text()), path


# ---- the decision order -----------------------------------------------------------


def test_a_failed_gate_is_out_however_cheap():
    d = choose([cand("low", 0.001, passed=False, note="failed: fixed"), cand("high", 0.05)])
    assert d.chosen.id == "high"
    assert {"id": "low", "status": "excluded", "why": "failed: fixed"} in d.ranked


def test_fewest_joules_per_good_answer_wins():
    d = choose([cand("a", 0.02), cand("b", 0.01), cand("c", 0.03)])
    assert d.chosen.id == "b" and not d.tie_broken_by_grid
    assert "lowest of 3" in d.reason


def test_pass_rate_counts_cheap_but_flaky_loses():
    # 0.010 Wh at 50% pass = 0.020 Wh per good answer: worse than 0.015 at 100%
    d = choose([cand("flaky", 0.010, pass_rate=0.5), cand("steady", 0.015)])
    assert d.chosen.id == "steady"


def test_zero_pass_rate_is_excluded():
    d = choose([cand("none", 0.001, pass_rate=0.0), cand("ok", 0.02)])
    assert d.chosen.id == "ok"
    assert any(r["why"].startswith("no good answers") for r in d.ranked)


def test_nothing_passes_means_no_route():
    d = choose([cand("a", 0.01, passed=False), cand("b", 0.01, passed=False)])
    assert d.chosen is None and "nothing to route to" in d.reason


def test_grid_breaks_a_tie_on_joules():
    cs = expand_regions([cand("x", 0.01)], ["us-east", "eu-north", "ap-south"])
    d = choose(cs, GRID)
    assert d.chosen.region == "eu-north" and d.tie_broken_by_grid
    assert d.grid["gco2_per_kwh"] == 35 and d.grid["label"] == "SIMULATED"
    assert "cleaner grid decided" in d.reason


def test_carbon_never_beats_joules_outside_the_band():
    clean_but_hungry = cand("hungry", 0.0104, region="eu-north")  # 4% more joules
    dirty_but_lean = cand("lean", 0.01, region="ap-south")
    d = choose([clean_but_hungry, dirty_but_lean], GRID, tie_pct=3.0)
    assert d.chosen.id == "lean"
    d = choose([clean_but_hungry, dirty_but_lean], GRID, tie_pct=5.0)
    assert d.chosen.id == "hungry"


def test_carbon_never_rescues_a_failed_gate():
    d = choose([cand("clean", 0.01, passed=False, region="eu-north"),
                cand("dirty", 0.01, region="ap-south")], GRID)
    assert d.chosen.id == "dirty"


def test_without_region_info_carbon_is_not_used():
    d = choose([cand("a", 0.01, usd=0.02), cand("b", 0.01, usd=0.01)], GRID)
    assert d.chosen.id == "b" and not d.tie_broken_by_grid  # cheaper USD, no grid claim


def test_equal_everything_is_stable_by_id():
    a = choose([cand("b", 0.01), cand("a", 0.01)])
    b = choose([cand("a", 0.01), cand("b", 0.01)])
    assert a.chosen.id == b.chosen.id == "a"


def test_range_energy_ranks_by_the_middle():
    ranged = Candidate("r", "b", "r", 0.01, 0.005, 0.05, 1.0, True)
    point = cand("p", 0.03)  # mid of range is 0.0275
    assert choose([point, ranged]).chosen.id == "r"


def test_bad_tie_pct():
    with pytest.raises(ValueError):
        choose([cand("a", 0.01)], tie_pct=-1)


def test_expand_regions_ids_and_regions():
    out = expand_regions([cand("x", 0.01)], ["a", "b"])
    assert [c.id for c in out] == ["x@a", "x@b"] and [c.region for c in out] == ["a", "b"]


# ---- candidates from a recorded run ------------------------------------------------


def test_candidates_from_a_cloud_receipt(cloud):
    c = cloud[0]
    arith = next(x for x in c["classes"] if x["class"] == "arith")
    cs = {x.effort: x for x in class_candidates(c, arith)}
    assert set(cs) == {"low", "medium", "xhigh"}
    assert cs["low"].gate_passed is False and "fixed" in cs["low"].gate_note
    assert cs["medium"].gate_passed and cs["xhigh"].is_reference
    assert cs["xhigh"].energy_label == "ESTIMATE" and cs["medium"].usd_label == "SIMULATED"
    assert cs["low"].wh_low < cs["low"].wh_high  # a range, never one number


# ---- replay -----------------------------------------------------------------------


def test_replay_matches_the_receipt_totals(cloud):
    c = cloud[0]
    rep = replay(c, GRID)
    t = rep["totals"]
    assert t["baseline"]["usd"] == pytest.approx(c["totals"]["would_have_cost_xhigh_usd"], abs=1e-6)
    assert t["effort_only"]["usd"] == pytest.approx(c["totals"]["tuned_usd"], abs=1e-6)
    assert rep["requests"] == c["totals"]["requests"]


def test_router_never_picks_a_failed_gate_in_replay(cloud):
    rep = replay(cloud[0], GRID)
    arith = next(x for x in rep["classes"] if x["class"] == "arith")
    assert arith["effort_only"]["effort"] == "medium"  # low was cheaper but failed the gate
    assert arith["effort_and_region"]["effort"] == "medium"


def test_region_changes_carbon_not_joules_or_usd(cloud):
    t = replay(cloud[0], GRID)["totals"]
    assert t["effort_only"]["j_mid"] == t["effort_and_region"]["j_mid"]
    assert t["effort_only"]["usd"] == t["effort_and_region"]["usd"]
    assert t["effort_and_region"]["gco2_mid"] < t["effort_only"]["gco2_mid"]
    assert t["effort_only"]["j_mid"] < t["baseline"]["j_mid"]


def test_replay_labels_are_honest(cloud):
    rep = replay(cloud[0], GRID)
    assert rep["label"] == "SIMULATED"
    assert rep["labels"] == {"usd": "SIMULATED", "joules": "ESTIMATE", "gco2": "SIMULATED",
                             "region": "SIMULATED"}
    text = json.dumps(rep).lower() + format_report(rep).lower()
    assert "neutral" not in text
    assert "measured" not in format_report(rep).lower().replace("not a measurement", "")
    assert any("not marginal" in n or "average" in n for n in rep["notes"])


def test_replay_is_repeatable(cloud):
    assert replay(cloud[0], GRID) == replay(cloud[0], GRID)


def test_replay_refuses_a_changed_or_wrong_receipt(cloud):
    bad = json.loads(json.dumps(cloud[0]))
    bad["totals"]["tuned_usd"] = 0.0
    with pytest.raises(ValueError, match="hash"):
        replay(bad, GRID)
    bad["integrity"]["sha256"] = canonical_sha256(bad)  # resealed: now it replays
    replay(bad, GRID)
    with pytest.raises(ValueError, match="not a cloud receipt"):
        replay({"schema": "x"}, GRID)


def test_replay_checks_regions(cloud):
    with pytest.raises(ValueError, match="home region"):
        replay(cloud[0], GRID, home_region="mars")
    with pytest.raises(ValueError, match="no grid reading"):
        replay(cloud[0], GRID, regions=["eu-north", "mars"])


def test_replay_with_one_home_region_only_changes_effort(cloud):
    rep = replay(cloud[0], GRID, regions=["us-east"])
    t = rep["totals"]
    assert t["effort_only"] == t["effort_and_region"]


# ---- grid sources -----------------------------------------------------------------


def test_static_grid_reads_fixture():
    r = GRID.reading("eu-north")
    assert r.gco2_per_kwh == 35 and r.label == "SIMULATED"
    assert GRID.reading("nowhere") is None and GRID.reading(None) is None


@pytest.mark.parametrize("doc", [
    "not json", '{"regions": {"a": {"gco2_per_kwh": -1}}}', '{"regions": {"a": {}}}',
    '{"label": "MEASURED", "regions": {"a": {"gco2_per_kwh": 1}}}'])
def test_bad_grid_fixture(tmp_path, doc):
    p = tmp_path / "g.json"
    p.write_text(doc)
    with pytest.raises(G.GridError):
        G.StaticGrid(p)


def uk_body(actual=63, forecast=58):
    return json.dumps({"data": [{"from": "2026-10-27T03:30Z", "to": "2026-10-27T04:00Z",
                                 "intensity": {"forecast": forecast, "actual": actual,
                                               "index": "low"}}]}).encode()


def test_uk_api_reading_is_cached_and_labelled_estimate():
    calls = []
    clock = [1000.0]

    def opener(url, timeout):
        calls.append(url)
        return uk_body()

    g = G.UkApiGrid(opener=opener, now=lambda: clock[0], ttl_s=60)
    r = g.reading("gb")
    assert (r.gco2_per_kwh, r.label, r.source) == (63.0, "ESTIMATE", "api.carbonintensity.org.uk")
    g.reading("gb")
    assert len(calls) == 1
    clock[0] += 61
    g.reading("gb")
    assert len(calls) == 2
    assert calls[0] == G.UK_URL


def test_uk_api_uses_forecast_when_no_actual_and_only_for_gb():
    called = []
    g = G.UkApiGrid(opener=lambda u, t: called.append(u) or uk_body(None, 77))
    assert g.reading("gb").gco2_per_kwh == 77.0
    called.clear()
    assert g.reading("eu-north").label == "SIMULATED"  # other regions come from the fixture
    assert called == []


@pytest.mark.parametrize("body", [b"garbage", b'{"data": []}', None])
def test_uk_api_failure_falls_back_to_fixture(body):
    def opener(u, t):
        if body is None:
            raise OSError("no network")
        return body

    r = G.UkApiGrid(opener=opener).reading("gb")
    assert r.label == "SIMULATED" and r.gco2_per_kwh == 150
    assert "UK API unavailable" in r.note


def test_make_grid():
    assert isinstance(G.make_grid("fixture"), G.StaticGrid)
    assert isinstance(G.make_grid("uk-api"), G.UkApiGrid)
    with pytest.raises(G.GridError):
        G.make_grid("other")


# ---- CLI --------------------------------------------------------------------------


def test_cli_replay_default_never_touches_the_network(cloud, monkeypatch, capsys, tmp_path):
    def boom(*a, **k):
        raise AssertionError("network used")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    out = tmp_path / "rep.json"
    assert main(["route", "replay", "--receipt", str(cloud[1]), "--out", str(out)]) == 0
    text = capsys.readouterr().out
    assert "no API call made" in text and "[SIMULATED]" in text and "always xhigh" in text
    assert json.loads(out.read_text())["format"] == "wattnot.route-replay/1"


def test_cli_replay_json(cloud, capsys):
    assert main(["route", "replay", "--receipt", str(cloud[1]), "--json",
                 "--regions", "us-east,eu-north"]) == 0
    rep = json.loads(capsys.readouterr().out)
    assert rep["regions"] == ["us-east", "eu-north"]


def test_cli_decide(cloud, capsys):
    assert main(["route", "decide", "--receipt", str(cloud[1]), "--class", "arith"]) == 0
    out = capsys.readouterr().out
    assert "medium in eu-north" in out and "grid [SIMULATED] eu-north" in out
    assert main(["route", "decide", "--receipt", str(cloud[1]), "--class", "arith",
                 "--json"]) == 0
    d = json.loads(capsys.readouterr().out)
    assert d["chosen"]["effort"] == "medium" and d["region_label"] == "SIMULATED"


def test_cli_decide_unknown_class(cloud):
    with pytest.raises(SystemExit):
        main(["route", "decide", "--receipt", str(cloud[1]), "--class", "nope"])


def test_cli_uk_api_path_with_a_fake_fetch(cloud, monkeypatch, capsys):
    monkeypatch.setattr(G.UkApiGrid, "_urlopen", staticmethod(lambda u, t: uk_body()))
    assert main(["route", "decide", "--receipt", str(cloud[1]), "--class", "arith",
                 "--regions", "gb,ap-south", "--grid", "uk-api"]) == 0
    out = capsys.readouterr().out
    assert "grid [ESTIMATE] gb: 63 gCO2/kWh" in out


def test_cli_grid(capsys):
    assert main(["route", "grid"]) == 0
    assert "eu-north" in capsys.readouterr().out
    assert main(["route", "grid", "--json"]) == 0
    assert len(json.loads(capsys.readouterr().out)) == 8


def test_cli_missing_receipt():
    with pytest.raises(SystemExit):
        main(["route", "replay", "--receipt", str(Path("/no/such/file.json"))])
