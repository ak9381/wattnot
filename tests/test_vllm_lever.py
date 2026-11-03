from wattnot.node.vllmlever import try_lever, flags_present
from wattnot.runner.vllm_server import TrialParams, server_args


class Check:
    def __init__(self, ok=True): self.ref, self.ok = None, ok
    def stock(self): self.ref = {"n": 5}
    def at(self, _): return {"accepted": self.ok, "energy_change_pct": -10.0 if self.ok else 3.0, "p99_change_pct": 0,
                             "answers_same": "5/5", "why_not": [] if self.ok else ["2 of 5 answers changed"], "label": "MEASURED"}


def rig(apply_args=True):
    st = {"argv": server_args("m", TrialParams())[3:], "restarts": []}
    def restart(argv):
        st["restarts"].append(argv)
        if apply_args or argv == server_args("m", TrialParams()):
            st["argv"] = argv[3:]
    return st, dict(restart=restart, running_argv=lambda: st["argv"], healthy=lambda: True, sleep=lambda s: None)


def test_fp8_passes_and_server_goes_back_to_stock():
    st, kw = rig()
    r = try_lever("fp8", model="m", stock=TrialParams(), check=Check(), **kw)
    assert r["kept"] and st["restarts"][0][-2:] == ["--quantization", "fp8"]
    assert st["argv"] == server_args("m", TrialParams())[3:]


def test_kv_cache_dtype_flag_exists():
    assert "--kv-cache-dtype" in server_args("m", TrialParams(kv_cache_dtype="fp8"))


def test_flag_that_never_applied_is_caught_and_rolled_back():
    st, kw = rig(apply_args=False)
    r = try_lever("batching", model="m", stock=TrialParams(), check=Check(), **kw)
    assert not r["kept"] and "not applied" in r["why"]
    assert st["restarts"][-1] == server_args("m", TrialParams())


def test_answer_change_rolls_back():
    st, kw = rig()
    r = try_lever("kv_mem", model="m", stock=TrialParams(), check=Check(ok=False), **kw)
    assert not r["kept"] and st["restarts"][-1] == server_args("m", TrialParams())


def test_no_check_keeps_stock_and_never_restarts():
    st, kw = rig()
    r = try_lever("fp8", model="m", stock=TrialParams(), check=None, **kw)
    assert not r["kept"] and st["restarts"] == []


def test_stacked_change_refused():
    st, kw = rig()
    r = try_lever("fp8", model="m", stock=TrialParams(quantization="fp8"), check=Check(), **kw)
    assert not r["kept"] and "stacked" in r["why"] and st["restarts"] == []


def test_flags_present():
    assert flags_present(["--max-num-seqs", "512"], ["--max-num-seqs", "512", "--enable-prefix-caching"]) == ["--enable-prefix-caching"]
