import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "verifier"))
import convert_receipts as cr  # noqa: E402
import wattnot_verify as wv  # noqa: E402


def test_pack_from_bundled_receipts(tmp_path):
    m = cr.convert(ROOT / "receipts", tmp_path)
    kinds = {e["kind"] for e in m["files"]}
    assert {"receipt", "addendum"} <= kinds
    for e in m["files"]:
        if e["kind"] == "receipt":
            r = wv.verify_path(tmp_path / e["file"])
            assert r["grade"] == e["grade"] and r["grade"] != "UNVERIFIABLE"
            src = Path(e["source"])
            assert (tmp_path / e["file"]).read_bytes() == src.read_bytes()
        if e["kind"] == "addendum":
            assert e["status"] == "ATTACHED", e
    assert json.loads((tmp_path / "MANIFEST.json").read_text())["format"] == "wattnot.judge-pack/1"


def test_tampered_addendum_is_broken(tmp_path):
    src = tmp_path / "src"
    sub = src / "run2"
    sub.mkdir(parents=True)
    for p in (ROOT / "receipts" / "run2").glob("rcpt_20261027_run2_cap350*"):
        (sub / p.name).write_bytes(p.read_bytes())
    a = sub / "rcpt_20261027_run2_cap350.wonder_live.json"
    d = json.loads(a.read_text())
    d["label"] = "MEASURED"
    a.write_text(json.dumps(d))
    m = cr.convert(src, tmp_path / "out")
    bad = [e for e in m["files"] if e["file"].endswith("wonder_live.json")]
    assert bad[0]["status"] == "BROKEN"


def test_cli(tmp_path):
    r = subprocess.run([sys.executable, "-m", "wattnot.cli", "receipt", "convert",
                        str(ROOT / "receipts"), "--out", str(tmp_path)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "MANIFEST" in r.stdout
