from wattnot import judge


def test_unknown_mode_prints_help(capsys):
    assert judge.main(["nope"]) == 1
    assert "python -m wattnot.judge" in capsys.readouterr().out


def test_missing_replay_lists_names(capsys):
    assert judge.run_replay("no-such-run") == 1
    assert "h100-max-1103" in capsys.readouterr().out


def test_savers_without_key_is_clear(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("META_API_KEY", "your-meta-api-key-here")
    assert judge.run_savers(tmp_path) == 1
    assert "META_API_KEY" in capsys.readouterr().out
