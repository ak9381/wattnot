"""The Muse Image eye set loads as wattnot.eyetest/1 and runs through the eye gate offline."""
import importlib.util
import json
from pathlib import Path

from wattnot.vision.assets import KINDS, load_eye_test
from wattnot.vision.run import main

ROOT = Path(__file__).resolve().parents[1]
SET = ROOT / "data" / "vision" / "eyeset-museimage"


def test_manifest_is_muse_image_and_verified():
    m = json.loads((SET / "answers.json").read_text())
    assert m["spec"] == "wattnot.eyetest/1"
    assert m["dataset_source"] == "Muse Image generated"
    assert m["generator"]["model"] == "muse-image-1.0"
    cats = {it["category"] for it in m["items"]}
    assert cats == {"gauge", "rack", "chart", "document"}
    assert 20 <= len(m["items"]) <= 30


def test_every_item_has_its_image_and_a_known_kind():
    eye = load_eye_test(SET, require_files=True)
    assert all(it.kind in KINDS and it.answer for it in eye)
    dropped = {p.stem for p in (SET / "dropped").glob("*.webp")}
    assert not dropped & set(eye.ids)  # misread images never ship


def test_eye_block_labels_the_dataset_source(tmp_path):
    rc = main(["--fake", "--assets", str(SET), "--out", str(tmp_path)])
    out = json.loads((tmp_path / "eye_test.json").read_text())
    assert rc == 0
    assert out["dataset_source"] == "Muse Image generated"
    assert out["answers_label"] == "MOCK"
    assert out["dataset_size"] == out["detail"]["checked"]


def test_generator_refuses_without_go(monkeypatch):
    monkeypatch.delenv("WATTNOT_GEN_VISION_GO", raising=False)
    spec = importlib.util.spec_from_file_location("g", ROOT / "scripts" / "gen_museimage_eyeset.py")
    g = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(g)
    assert g.main() == 2
    m = g.build_manifest([], image_calls=0)
    assert m["dataset_source"] == "Muse Image generated" and m["label"] == "SYNTHETIC"
