import pytest

from wattnot.cli import main
from wattnot.impact import estimate


def test_math():
    i = estimate(0.01, 100_000, 8)  # 0.01 Wh x 100k req x 8 GPUs x 30 days
    assert i.kwh == pytest.approx(240.0)
    assert i.usd == pytest.approx(28.8) and i.litres == pytest.approx(432.0)
    j = estimate(0.01, 100_000, 8, pue=1.5, tariff_usd_per_kwh=0.1, wue_l_per_kwh=0, days=1)
    assert j.kwh == pytest.approx(12.0) and j.usd == pytest.approx(1.2) and j.litres == 0


def test_bad_inputs():
    with pytest.raises(ValueError):
        estimate(0.01, 1, 0)
    with pytest.raises(ValueError):
        estimate(0.01, 1, 1, pue=0.9)


def test_cli_every_line_is_estimate(capsys):
    assert main(["impact", "--wh-delta", "0.01", "--requests-per-gpu-per-day", "100000",
                 "--gpus", "8"]) == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert all(x.startswith("ESTIMATE") for x in lines[:4])
    assert "Recommended actions:" in lines
    assert "240.0 kWh" in lines[0] and "$28.80" in lines[1] and "432 L" in lines[2]


def test_cli_negative_delta_says_added(capsys):
    main(["impact", "--wh-delta", "-0.002", "--requests-per-gpu-per-day", "1000"])
    assert "energy added: 0.1 kWh" in capsys.readouterr().out


def test_cli_requires_measured_delta(capsys):
    with pytest.raises(SystemExit):
        main(["impact", "--requests-per-gpu-per-day", "10"])
    with pytest.raises(SystemExit):
        main(["impact", "--wh-delta", "1", "--requests-per-gpu-per-day", "10", "--gpus", "0"])


def test_no_rupees_anywhere(capsys):
    main(["impact", "--wh-delta", "0.01", "--requests-per-gpu-per-day", "100000",
          "--tariff-usd-per-kwh", "0.2"])
    out = capsys.readouterr().out
    assert "$" in out and "INR" not in out and "\u20b9" not in out
    with pytest.raises(SystemExit):
        main(["impact", "--wh-delta", "1", "--requests-per-gpu-per-day", "1",
              "--tariff-inr-per-kwh", "8"])
