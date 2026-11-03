"""The datasets picked in the launch UI reach the stage runners as --sets."""
from wattnot.dash import launch as L


def _cfg(prov, sets):
    return L.norm_config({"provider": prov, "datasets": sets, "rounds": 1})


def test_modal_public_sets_passed():
    cmd, _, _ = L.command(_cfg("modal_a100", ["gsm8k", "humaneval"]), "live")
    i = cmd.index("--sets")
    assert cmd[i + 1:i + 3] == ["gsm8k", "humaneval"]


def test_baseten_our_set_passed():
    cmd, _, _ = L.command(_cfg("baseten", ["ours"]), "live")
    assert cmd[cmd.index("--sets") + 1] == "ours"


def test_unknown_sets_fall_back_to_runner_default():
    cmd, _, _ = L.command(_cfg("baseten", ["mixed"]), "live")
    assert "--sets" not in cmd
