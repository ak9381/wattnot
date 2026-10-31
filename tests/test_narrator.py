"""The Muse Spark narration worker (wattnot.dash.narrator) and its wiring into the replay.

A FAKE client stands in for the guarded Muse Spark client: no network, no spend. The tests check
that narrate() returns muse-spark/LIVE entries for the pick steps with a model id and trace id,
that the cache round-trips, that plan() with a cache present emits a step.progress narration
labelled LIVE for those steps, and that plan() WITHOUT a cache still emits only template lines.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from wattnot.dash import narrator, runplan

ROOT = Path(__file__).resolve().parents[1]
STORY = ROOT / "data" / "dash" / "story.json"
pytestmark = pytest.mark.skipif(not STORY.is_file(), reason="story bundle not built")

NOW = datetime(2026, 10, 31, 12, 0, 0, tzinfo=UTC)  # fixed so run_id (and cache path) is stable


@pytest.fixture(scope="module")
def bundle():
    return json.loads(STORY.read_text())


class FakeClient:
    """A canned Chat Completions reply, counting calls. Never touches the network."""

    def __init__(self, content="A short live narration of this step.", reply_id="resp_fake_123",
                 model="muse-spark-1.3"):
        self.content, self.reply_id, self.model = content, reply_id, model
        self.calls = []

    def __call__(self, body, *, purpose="unspecified"):
        self.calls.append((body, purpose))
        return {"id": self.reply_id, "model": self.model,
                "choices": [{"message": {"content": self.content}}],
                "usage": {"prompt_tokens": 42, "completion_tokens": 11}}


def _frames(bundle):
    return runplan.plan(bundle, {"model": "glimmer"}, now=NOW)["events"]


# ---------------------------------------------------------------- narrate()

def test_narrated_step_ids_are_room_pick_and_sweep_pick(bundle):
    ids = narrator.narrated_step_ids(_frames(bundle))
    assert ids == ["room.pick#1", "sweep.pick#1"]


def test_narrate_returns_muse_spark_live_entries(bundle):
    fake = FakeClient()
    out = narrator.narrate(_frames(bundle), client=fake)
    assert set(out) == {"room.pick#1", "sweep.pick#1"}
    for sid, n in out.items():
        assert n["text"] == "A short live narration of this step."
        assert n["source"] == "muse-spark"
        assert n["label"] == "LIVE"
        assert n["model"] == "muse-spark-1.3"
        assert n["trace_id"] == "resp_fake_123"
    # one call per narrated step, and the facts (the rack id) made it into the prompt
    assert len(fake.calls) == 2
    assert fake.calls[0][1] == "dash-narrate"
    joined = "".join(json.dumps(b["messages"]) for b, _ in fake.calls)
    assert "cap" in joined.lower() or "rack" in joined.lower()


def test_narrate_skips_steps_with_empty_or_broken_replies(bundle):
    class Empty(FakeClient):
        def __call__(self, body, *, purpose="unspecified"):
            return {"id": "x", "choices": [{"message": {"content": "   "}}]}
    assert narrator.narrate(_frames(bundle), client=Empty()) == {}

    class Boom(FakeClient):
        def __call__(self, body, *, purpose="unspecified"):
            raise RuntimeError("provider down")
    assert narrator.narrate(_frames(bundle), client=Boom()) == {}  # never raises


def test_narrate_synthesises_a_labelled_trace_id_when_the_reply_has_none(bundle):
    class NoId(FakeClient):
        def __call__(self, body, *, purpose="unspecified"):
            return {"model": "muse-spark-1.3",
                    "choices": [{"message": {"content": "live line"}}], "usage": {}}
    out = narrator.narrate(_frames(bundle), client=NoId())
    for n in out.values():
        assert n["trace_id"].startswith("synth-")  # honestly labelled, not passed off as real


# ---------------------------------------------------------------- the cache layer

def test_generate_and_cache_round_trips(bundle, tmp_path):
    cache = tmp_path / "run.json"
    written = narrator.generate_and_cache(_frames(bundle), cache, client=FakeClient())
    assert cache.is_file()
    loaded = narrator.load_cache(cache)
    assert loaded == written
    assert set(loaded) == {"room.pick#1", "sweep.pick#1"}


def test_load_cache_missing_file_is_empty(tmp_path):
    assert narrator.load_cache(tmp_path / "nope.json") == {}


def test_load_cache_drops_mislabelled_entries(tmp_path):
    cache = tmp_path / "bad.json"
    cache.write_text(json.dumps({
        "room.pick#1": {"text": "fine", "source": "muse-spark", "model": "muse-spark-1.3",
                        "trace_id": "t1"},
        "sweep.pick#1": {"text": "nope", "source": "template", "model": "x", "trace_id": "t2"},
        "other": {"text": "", "source": "muse-spark", "model": "x", "trace_id": "t3"}}))
    loaded = narrator.load_cache(cache)
    assert set(loaded) == {"room.pick#1"}  # template-sourced and empty-text entries dropped
    assert loaded["room.pick#1"]["label"] == "LIVE"


def test_cache_path_for_keys_on_stable_config_hash():
    # The timestamp in a run_id changes every plan() call, so the cache keys on the trailing
    # config hash: two timestamps of the same config resolve to the same cache file.
    a = narrator.cache_path_for("run_20261031_120000_4604f3", root="/tmp/narr")
    b = narrator.cache_path_for("run_20261031_235959_4604f3", root="/tmp/narr")
    assert a == b == Path("/tmp/narr/4604f3.json")
    # A run_id without the timestamped shape keys on itself.
    assert narrator.cache_path_for("run_x", root="/tmp/narr") == Path("/tmp/narr/x.json")


# ---------------------------------------------------------------- plan() wiring

def _steps_with_live(events):
    """{step_id: narration} for every step.progress carrying a muse-spark narration."""
    out = {}
    for f in events:
        e = f["event"]
        n = e["data"].get("narration")
        if e["type"] == "step.progress" and n and n.get("source") == "muse-spark":
            out[e["step_id"]] = n
    return out


def test_plan_without_a_cache_emits_only_template_narration(bundle):
    events = runplan.plan(bundle, {"model": "glimmer"}, now=NOW)["events"]
    assert _steps_with_live(events) == {}  # offline fallback: nothing labelled LIVE
    for f in events:
        e = f["event"]
        if e["type"] == "step.started":
            assert e["data"]["narration"]["source"] == "template"


def test_plan_narration_false_matches_default(bundle):
    a = runplan.plan(bundle, {"model": "glimmer"}, now=NOW)["events"]
    b = runplan.plan(bundle, {"model": "glimmer"}, now=NOW, narration=False)["events"]
    assert len(a) == len(b)


def test_plan_with_a_cache_emits_live_narration_for_the_pick_steps(bundle, tmp_path):
    frames = _frames(bundle)
    cache = tmp_path / "run.json"
    narrator.generate_and_cache(frames, cache, client=FakeClient())

    p = runplan.plan(bundle, {"model": "glimmer"}, now=NOW, narration=str(cache))
    events = p["events"]
    live = _steps_with_live(events)
    assert set(live) == {"room.pick#1", "sweep.pick#1"}
    for sid, n in live.items():
        assert n["label"] == "LIVE"
        assert n["model"] == "muse-spark-1.3"
        assert n["trace_id"] == "resp_fake_123"
        assert n["text"]
    # the template line is still emitted on step.started (the overlay replaces it in the view)
    started = {f["event"]["step_id"]: f["event"]["data"]["narration"]
               for f in events if f["event"]["type"] == "step.started"}
    assert started["room.pick#1"]["source"] == "template"
    # the overlay frame comes right after its started frame, same step_id
    order = [(f["event"]["type"], f["event"]["step_id"]) for f in events]
    i = order.index(("step.started", "room.pick#1"))
    assert order[i + 1] == ("step.progress", "room.pick#1")
    # seqs stay dense and ordered after the insert
    seqs = [f["event"]["seq"] for f in events]
    assert seqs == list(range(len(seqs)))
    assert p["results"]["narration"]["steps"] == ["room.pick#1", "sweep.pick#1"]


def test_plan_auto_detect_reads_the_default_cache_path(bundle, tmp_path, monkeypatch):
    frames = _frames(bundle)
    run_id = runplan.plan(bundle, {"model": "glimmer"}, now=NOW)["run_id"]
    monkeypatch.setattr(narrator, "CACHE_ROOT", tmp_path)
    narrator.generate_and_cache(frames, narrator.cache_path_for(run_id), client=FakeClient())

    p = runplan.plan(bundle, {"model": "glimmer"}, now=NOW, narration=True)
    assert set(_steps_with_live(p["events"])) == {"room.pick#1", "sweep.pick#1"}
