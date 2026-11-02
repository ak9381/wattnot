"""r04f rule (Akash, 2 Nov 14:23 IST): a failed LangChain block must not stop the round; every
other block still runs to the end. Cost caps and crash stops stay."""
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "e2e"))
import modal_full_inner as I  # noqa: E402


def test_failed_langchain_block_lets_the_rest_run(tmp_path):
    out = tmp_path / "r"
    env = {**os.environ, "PYTHONPATH": f"{ROOT}:{ROOT / 'src'}", "WATTNOT_FAKE_FAIL": "langchain"}
    subprocess.run([sys.executable, str(ROOT / "scripts" / "e2e" / "modal_full_inner.py"), "--fake", "--limit", "1",
                    "--out", str(out), "--skip", "fp8"], cwd=ROOT, env=env, capture_output=True, timeout=700)
    doc = json.loads((out / "inner.json").read_text())
    blocks = doc["steps"]["blocks"]
    assert blocks["langchain"]["rc"] != 0
    others = [k for k in I.block_cmds(1, True, out) if k != "langchain"]
    assert others and all(blocks[k]["rc"] == 0 for k in others), blocks
    assert not doc.get("fatal")


def test_block_crash_is_recorded_not_raised(tmp_path):
    r = I.sub(["/nonexistent/binary"], tmp_path / "x", 5)
    assert r["rc"] == 125
