import json
from pathlib import Path

from wattnot.meta.wonder import DEFAULT_MODEL, WONDER_SCHEMA
from wattnot.musecode.apply_pr import DEFAULT_MODEL as MC_MODEL
from wattnot.receipt.canon import canonical_sha256
from wattnot.receipt.sign import verify_offline_bundle

R = Path(__file__).resolve().parents[1] / "receipts/run2"
REAL_IDS = {"muse-wonder-1.0", "muse-spark-1.3", "muse-spark-1.3-contributor", "muse-spark-1.2"}


def test_wonder_strict_schema_requires_every_key():
    assert set(WONDER_SCHEMA["required"]) == set(WONDER_SCHEMA["properties"])


def test_model_ids_are_real():
    assert DEFAULT_MODEL in REAL_IDS and MC_MODEL in REAL_IDS


def test_addenda_verify_and_point_at_run2():
    parent = json.loads((R / "rcpt_20261027_run2_cap350.json").read_text())
    for n in ("wonder_live", "musecode_pr", "sam_hotspot", "voice_transcribe"):
        d = json.loads((R / f"rcpt_20261027_run2_cap350.{n}.json").read_text())
        b = json.loads((R / f"rcpt_20261027_run2_cap350.{n}.sigstore.json").read_text())
        assert d["integrity"]["sha256"] == canonical_sha256(d)
        assert verify_offline_bundle(d, b)[0]
        assert d["parent"]["sha256"] == parent["integrity"]["sha256"]
        assert d["label"] == "LIVE"


def test_live_labels_stay_honest():
    s = json.loads((R / "rcpt_20261027_run2_cap350.sam_hotspot.json").read_text())["data"]
    assert s["labels"]["heat_attribution"] == "SIMULATED" and s["labels"]["cap_decision"] == "ADVICE"
    v = json.loads((R / "rcpt_20261027_run2_cap350.voice_transcribe.json").read_text())["data"]
    assert v["audio"]["source"].startswith("SYNTHETIC")
