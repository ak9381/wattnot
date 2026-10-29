"""Every model WattNot runs is a Meta model (rule of Oct 29)."""
from pathlib import Path

from wattnot import models
from wattnot.longrun import driver
from wattnot.moe import adaptive_topk
from wattnot.proof import session

ROOT = Path(__file__).resolve().parents[1]


def test_served_models_are_meta():
    for m in models.MODELS.values():
        assert m.repo.split("/")[0] in {"meta-llama", "meta-models"}, m.repo


def test_topk_default_is_scout_and_setup_pulls_no_non_meta():
    assert adaptive_topk.DEFAULT_MODEL == "meta-llama/Llama-4-Scout-17B-16E-Instruct"
    setup = (ROOT / "scripts/h100_vm_setup.sh").read_text()
    assert 'MOE="${2:-none}"' in setup and "granite" not in setup.lower()


def test_no_topk_block_on_the_h100(tmp_path):
    for plan in (driver.plan_glimmer(tmp_path), driver.plan(tmp_path)):
        assert not any("top-k" in b.title.lower() or "granite" in b.title.lower() for b in plan)


def test_quality_gate_is_one_point():
    assert session.MAX_DROP_PTS == 1.0
