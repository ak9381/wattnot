import pytest

from wattnot.meter import crosscheck as C


def test_mock_mode_runs_all_sources_and_computes_gaps():
    # NVML: 0.1 Wh over 3600 s -> mean 0.1 W. Mock base defaults to that mean.
    out = C.cross_check(0.1, 3600, mock=True)
    assert out["mock"] and out["nvml_mean_w"] == 0.1
    by = {r["source"]: r for r in out["sources"]}
    assert set(by) == {"nvidia-smi", "dcgm", "ipmi"}
    assert all(r["available"] for r in out["sources"])
    # gpu sources sit ~1% off NVML; node source ~60% high and flagged as node scope
    assert by["nvidia-smi"]["gap_pct_vs_nvml"] == 1.0 and by["nvidia-smi"]["scope"] == "gpu"
    assert by["dcgm"]["gap_pct_vs_nvml"] == -1.0
    assert by["ipmi"]["scope"] == "node" and by["ipmi"]["gap_pct_vs_nvml"] > 50
    assert out["worst_gpu_gap_pct"] == 1.0 and out["gpu_sources_agree"] is True
    assert all(r["label"] == "MOCK" for r in out["sources"])


def test_missing_source_is_recorded_not_fatal():
    present = C.Source("nvidia-smi", "gpu", lambda: 400.0)
    missing = C.Source("dcgm", "gpu", lambda: None)
    out = C.cross_check(0.1, 3600, sources=[present, missing])
    rows = {r["source"]: r for r in out["sources"]}
    assert rows["dcgm"]["available"] is False and rows["dcgm"]["gap_pct_vs_nvml"] is None
    assert out["available_sources"] == ["nvidia-smi"]


def test_reader_exception_becomes_unavailable():
    def boom():
        raise OSError("no device")
    out = C.cross_check(0.1, 3600, sources=[C.Source("dcgm", "gpu", boom)])
    assert out["sources"][0]["available"] is False


def test_large_gpu_gap_fails_agreement():
    hot = C.Source("nvidia-smi", "gpu", lambda: 0.2 * 3600 / 3600 * 1000)  # way off
    out = C.cross_check(0.1, 3600, sources=[hot])
    assert out["gpu_sources_agree"] is False


def test_parsers_handle_real_text():
    assert C.nvidia_smi_w(run=lambda cmd: "350.12\n360.00\n") == pytest.approx(710.12)
    assert C.ipmi_w(run=lambda cmd: "Instantaneous power reading:   612 Watts\n") == 612.0
    assert C.dcgm_w(run=lambda cmd: "# Entity\nGPU 0\n   0   355.0\n   1   340.0\n") == 695.0


def test_zero_seconds_rejected():
    with pytest.raises(ValueError):
        C.cross_check(0.1, 0, mock=True)
