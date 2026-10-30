from pathlib import Path
import json

from wattnot.longrun import nomock


def test_banned_tokens_flags_mock_switches_but_allows_plain_auto_approve():
    assert nomock._banned_tokens(["python", "-m", "x", "--fake"]) == ["--fake"]
    assert "--sam replay" in nomock._banned_tokens(["orch", "--sam", "replay"])
    assert "replay" in nomock._banned_tokens(["orch", "replay"])
    assert nomock._banned_tokens(["orch", "--auto-approve"]) == []  # plain is allowed
    assert "--auto-approve-as-proof" in nomock._banned_tokens(["orch", "--auto-approve-as-proof"])


def test_source_ok():
    assert nomock._source_ok("GENERATED-META")
    assert nomock._source_ok("TTS-espeak-ng")
    assert nomock._source_ok("MEASURED")
    assert not nomock._source_ok("placeholder-offline")
    assert not nomock._source_ok("stub-offline")
    assert not nomock._source_ok("")  # unknown provenance is not real


def test_guard_on_the_committed_assets(tmp_path):
    # Spoken WAVs are now real TTS audio, so no stub finding. The eye-test images pass only
    # once the paid generation has stamped them; until then the guard must fail on them.
    r = nomock.audit_plan("glimmer-test", tmp_path)
    assert not any("stub" in f["what"] for f in r["findings"])
    root = Path(__file__).resolve().parents[1]
    eye = json.loads((root / "data/vision/eyetest/answers.json").read_text())
    if eye.get("source") == "placeholder-offline":
        assert r["ok"] is False
        assert any("eye-test images are not real" in f["what"] for f in r["findings"])


def _write_real_assets(tmp_path):
    eye = tmp_path / "eye"
    eye.mkdir()
    (eye / "img.png").write_bytes(b"\x89PNG" + b"0" * 2048)
    (eye / "answers.json").write_text(json.dumps({
        "label": "SYNTHETIC", "source": "GENERATED-META",
        "items": [{"id": "a", "file": "img.png", "kind": "text", "question": "q", "answer": "x"}],
    }))
    spk = tmp_path / "spk"
    spk.mkdir()
    (spk / "a.wav").write_bytes(b"RIFF" + b"0" * (20 * 1024))
    (spk / "answers.json").write_text(json.dumps({
        "label": "SYNTHETIC", "source": "TTS-espeak-ng",
        "items": [{"id": "a", "file": "a.wav", "kind": "text", "noise": "clean",
                   "question": "q", "answer": "x"}],
    }))
    return eye, spk


def test_guard_passes_when_assets_are_real(tmp_path, monkeypatch):
    eye, spk = _write_real_assets(tmp_path)
    monkeypatch.setattr("wattnot.vision.assets.DEFAULT_ASSETS_DIR", eye)
    monkeypatch.setattr("wattnot.spokenanswer.assets.DEFAULT_ASSETS_DIR", spk)
    r = nomock.audit_plan("glimmer-test", tmp_path)
    assert r["ok"] is True, r["findings"]
    assert "t1-eye" in r["checked"] and "t2-fp8" in r["checked"]


def test_cli_exit_codes(tmp_path, monkeypatch):
    ph = tmp_path / "ph"
    ph.mkdir()
    (ph / "img.png").write_bytes(b"\x89PNG" + b"0" * 2048)
    (ph / "answers.json").write_text(json.dumps({"label": "SYNTHETIC", "source": "placeholder-offline",
        "items": [{"id": "a", "file": "img.png", "kind": "text", "question": "q", "answer": "x"}]}))
    monkeypatch.setattr("wattnot.vision.assets.DEFAULT_ASSETS_DIR", ph)
    assert nomock.main(["--model", "glimmer-test"]) == 3           # placeholder images present
    eye, spk = _write_real_assets(tmp_path)
    monkeypatch.setattr("wattnot.vision.assets.DEFAULT_ASSETS_DIR", eye)
    monkeypatch.setattr("wattnot.spokenanswer.assets.DEFAULT_ASSETS_DIR", spk)
    assert nomock.main(["--model", "glimmer-test", "--out", str(tmp_path / "o")]) == 0
    assert (tmp_path / "o" / "nomock-preflight.json").exists()
