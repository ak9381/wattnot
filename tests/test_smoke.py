"""Smoke test: the package imports and the CLI reports its version."""

import wattnot
from wattnot.cli import build_parser, main


def test_version_is_set():
    assert wattnot.__version__


def test_version_flag_exits_zero(capsys):
    # --version uses argparse's version action, which exits 0.
    parser = build_parser()
    try:
        parser.parse_args(["--version"])
    except SystemExit as exc:
        assert exc.code == 0
    out = capsys.readouterr().out
    assert wattnot.__version__ in out


def test_main_runs_without_args():
    assert main([]) == 0
