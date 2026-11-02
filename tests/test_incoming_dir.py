"""Copy-back folder: a stray 0-byte `_incoming` file (left by `modal volume get` into a missing
dest) must not break the next run's results pull."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "e2e"))
import modal_full_round as R  # noqa: E402
from modal_full_round import full_stage as FS  # noqa: E402


def test_ensure_dir_clears_empty_file(tmp_path):
    p = tmp_path / "_incoming"
    p.write_text("")
    FS.ensure_dir(p)
    assert p.is_dir()
    FS.ensure_dir(p)   # idempotent
    assert p.is_dir()


def test_ensure_dir_keeps_nonempty_file(tmp_path):
    p = tmp_path / "_incoming"
    p.write_text("data")
    with pytest.raises(FileExistsError):
        FS.ensure_dir(p)
    assert p.read_text() == "data"


def test_recover_partial_makes_dest_dir_first(tmp_path):
    inc = tmp_path / "_incoming"
    seen = []

    def fetch(tag, dest):
        seen.append(dest.is_dir())
    assert R.recover_partial("r-1", inc, fetch=fetch) is None
    assert seen == [True]


def test_runners_and_entrypoint_use_ensure_dir():
    for f in ("scripts/e2e/modal_full_round.py", "scripts/e2e/modal_block_run.py", "modal/full_stage.py"):
        assert "ensure_dir(" in (ROOT / f).read_text(), f
    src = (ROOT / "modal" / "full_stage.py").read_text()
    pull = src[src.index("def round_("):]
    assert "ensure_dir(out)" in pull and "os.makedirs(out" not in pull
