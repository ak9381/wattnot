"""A live file appended on the VM between the sha listing and the copy is a verified prefix
snapshot ("growing"), not a copy failure (retest 31 Oct: gpu_trace.csv)."""
import hashlib
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "run2_remote", Path(__file__).parents[1] / "scripts" / "run2_remote.py")
R = importlib.util.module_from_spec(spec)
spec.loader.exec_module(R)


def test_prefix_match_is_growing_and_real_mismatch_stays(tmp_path):
    (tmp_path / "gpu_trace.csv").write_bytes(b"t,w\n1,300\n")
    (tmp_path / "results.json").write_bytes(b"{}")
    remote = {"gpu_trace.csv": b"t,w\n1,300\n2,301\n", "results.json": b'{"x":1}'}

    def run_remote(cmd):
        n = int(cmd.split("head -c ")[1].split()[0])
        name = cmd.split()[-3]
        return hashlib.sha256(remote[name][:n]).hexdigest() + "  -\n"

    real, growing = R.split_growing(tmp_path, ["gpu_trace.csv", "results.json", "gone.txt"],
                                    run_remote)
    assert growing == ["gpu_trace.csv"]
    assert real == ["results.json", "gone.txt"]


def test_unreachable_vm_keeps_it_flagged(tmp_path):
    (tmp_path / "a.log").write_bytes(b"x")

    def boom(cmd):
        raise OSError("down")

    assert R.split_growing(tmp_path, ["a.log"], boom) == (["a.log"], [])
