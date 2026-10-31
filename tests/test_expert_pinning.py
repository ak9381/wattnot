"""Per-expert pinning: hot/cold classification, map serialization, launch args,
and the guarded vLLM loader. All offline with fakes -- no GPU, no real vLLM.
"""

import json
import types

import pytest

from wattnot.spark_open.expert_pin import (
    ENV_PIN_MAP,
    MAP_LABEL,
    PlacementMap,
    build_placement_map,
    launch_args,
    map_env,
    map_from_knobs,
    read_placement_map,
    register,
    write_placement_map,
)
from wattnot.spark_open.knobs import SparkKnobs
from wattnot.spark_open.placement import ExpertPlacement


def test_hot_cold_classification_top_n_and_tiebreak():
    # (3,7) and (0,2) are the two hottest; ties (count 5) break by (layer,expert).
    counts = {(0, 1): 5, (0, 2): 9, (3, 7): 12, (5, 0): 5}
    m = build_placement_map(counts, vram_slots=2)
    assert m.hot == ((3, 7), (0, 2))          # top-2 by count, desc
    assert m.cold == ((0, 1), (5, 0))         # rest, count tie -> (layer,expert) order
    assert m.vram_slots == 2 and m.label == MAP_LABEL
    # Every observed expert is placed somewhere, no duplicates.
    assert set(m.hot) | set(m.cold) == set(counts)
    assert not (set(m.hot) & set(m.cold))


def test_classification_slots_zero_and_oversized():
    counts = {(0, 1): 3, (1, 1): 2}
    assert build_placement_map(counts, 0).hot == ()
    assert set(build_placement_map(counts, 0).cold) == set(counts)
    big = build_placement_map(counts, 99)
    assert set(big.hot) == set(counts) and big.cold == ()


def test_no_counts_is_empty_with_layer_reason():
    m = build_placement_map(None, 8)
    assert m.hot == () and m.cold == () and "llama.cpp" in m.reason


def test_map_from_knobs_uses_pinned_experts():
    k = SparkKnobs(pinned_experts=((0, 1), (4, 9)))
    m = map_from_knobs(k)
    assert m.hot == ((0, 1), (4, 9)) and m.vram_slots == 2 and m.cold == ()
    assert map_from_knobs(SparkKnobs()).hot == ()


def test_serialization_round_trips(tmp_path):
    m = build_placement_map({(0, 2): 9, (3, 7): 12, (0, 1): 1}, 2)
    p = write_placement_map(m, tmp_path / "sub" / "map.json")
    assert p.is_file()
    on_disk = json.loads(p.read_text())
    assert on_disk["version"] == 1 and on_disk["vram_slots"] == 2
    assert on_disk["hot"] == [[3, 7], [0, 2]] and on_disk["label"] == MAP_LABEL
    back = read_placement_map(p)
    assert back.hot == m.hot and back.cold == m.cold and back.vram_slots == m.vram_slots
    assert PlacementMap.from_dict(m.to_dict()) == m  # pure round-trip


def test_launch_args_and_env(tmp_path):
    m = build_placement_map({(0, 2): 9}, 1)
    p = tmp_path / "pin.json"
    assert launch_args(m, p) == ["--env", f"{ENV_PIN_MAP}={p}"] and p.is_file()
    assert map_env(m, p) == {ENV_PIN_MAP: str(p)}
    # Nothing to pin -> no flags, no file written.
    empty = build_placement_map(None, 4)
    none_path = tmp_path / "none.json"
    assert launch_args(empty, none_path) == [] and map_env(empty, none_path) == {}
    assert not none_path.exists()


def test_register_guarded_when_vllm_absent(monkeypatch):
    # Simulate vLLM missing: register must not raise, returns a clean status.
    import builtins
    real_import = builtins.__import__

    def no_vllm(name, *a, **k):
        if name == "vllm":
            raise ImportError("No module named 'vllm'")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", no_vllm)
    st = register()
    assert not st.installed and st.backend == "none" and st.label == MAP_LABEL
    assert "vLLM not installed" in st.reason
    assert st.as_dict()["details"]["env_var"] == ENV_PIN_MAP


def test_register_with_fake_vllm_publishes_map(tmp_path, monkeypatch):
    monkeypatch.delenv(ENV_PIN_MAP, raising=False)
    fake_vllm = types.SimpleNamespace(__version__="0.0-fake")
    m = build_placement_map({(0, 2): 9, (3, 7): 12}, 2)
    mp = tmp_path / "pin.json"
    st = register(m, map_path=mp, vllm_module=fake_vllm)
    assert st.installed and st.backend == "vllm" and st.label == MAP_LABEL
    assert st.details["vllm_version"] == "0.0-fake" and st.details["hot_experts"] == 2
    assert st.details["map_path"] == str(mp) and mp.is_file()
    # The patch reads the map from this env var; register must set it.
    import os
    assert os.environ[ENV_PIN_MAP] == str(mp)
    assert read_placement_map(mp).hot == m.hot


def test_register_fake_vllm_no_placement_sets_no_env(tmp_path, monkeypatch):
    monkeypatch.delenv(ENV_PIN_MAP, raising=False)
    fake_vllm = types.SimpleNamespace(__version__="0.0-fake")
    st = register(None, map_path=tmp_path / "x.json", vllm_module=fake_vllm)
    assert st.installed and st.backend == "vllm"
    import os
    assert ENV_PIN_MAP not in os.environ


def test_expert_placement_args_returns_flags(tmp_path):
    mp = tmp_path / "pin.json"
    ep = ExpertPlacement({(0, 1): 3, (2, 4): 9}, 1, map_path=mp)
    assert ep.plan["mode"] == "expert" and ep.plan["pinned"] == [[2, 4]]
    args = ep.args(SparkKnobs())
    assert args == ["--env", f"{ENV_PIN_MAP}={mp}"] and mp.is_file()
    on_disk = read_placement_map(mp)
    assert on_disk.hot == ((2, 4),) and on_disk.cold == ((0, 1),)


def test_expert_placement_falls_back_to_knob_pins(tmp_path):
    # No routing counts, but the knobs carry pinned experts -> use those.
    mp = tmp_path / "pin.json"
    ep = ExpertPlacement({}, 2, map_path=mp)
    k = SparkKnobs(pinned_experts=((1, 2), (3, 4)))
    assert ep.args(k) == ["--env", f"{ENV_PIN_MAP}={mp}"]
    assert read_placement_map(mp).hot == ((1, 2), (3, 4))
    # No counts and no knob pins -> no flags.
    assert ExpertPlacement({}, 2, map_path=tmp_path / "n.json").args(SparkKnobs()) == []


def test_patch_file_exists_and_is_honest():
    from pathlib import Path

    import wattnot.spark_open as pkg
    patch = Path(pkg.__file__).parent / "patches" / "expert_pinning.patch"
    text = patch.read_text()
    assert ENV_PIN_MAP in text                      # patch and loader agree on the env var
    assert "SIMULATED" in text and "NOT APPLIED" in text  # honest about being unrun
    assert "fused_moe" in text                      # targets a plausible vLLM module
