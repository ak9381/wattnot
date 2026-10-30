"""Review round 3: copy checksums, no-mock laundering, setup preflight wiring."""
import hashlib
import importlib.util
from pathlib import Path

from wattnot.longrun import nomock

ROOT = Path(__file__).resolve().parents[1]


def _remote():
    spec = importlib.util.spec_from_file_location("r2", ROOT / "scripts" / "run2_remote.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_verify_copy_flags_missing_and_changed(tmp_path):
    (tmp_path / "a.json").write_text("ok")
    (tmp_path / "b.json").write_text("truncated")
    sums = {"a.json": hashlib.sha256(b"ok").hexdigest(),
            "b.json": hashlib.sha256(b"truncated-not").hexdigest(),
            "c.json": "0" * 64}
    assert _remote().verify_copy(tmp_path, sums) == ["b.json", "c.json"]


def test_real_prefix_does_not_launder_a_mock_source():
    assert not nomock._source_ok("live-replay-fixtures")
    assert not nomock._source_ok("real-stub")
    assert nomock._source_ok("h100-run1") and nomock._source_ok("tts-espeak-ng")


def test_setup_checks_docker_hf_and_clock_before_download():
    s = (ROOT / "scripts" / "h100_vm_setup.sh").read_text()
    pre = s[: s.index("--- install")]
    assert "NVIDIA runtime" in pre and "timedatectl set-ntp" in pre
    assert '"meta-llama" ]; then' not in pre   # the HF check runs for any model
