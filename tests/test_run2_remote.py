"""The control-machine setup runner, against a fake SSH client that runs commands locally."""

from __future__ import annotations

import importlib.util
import io
import os
import stat
import subprocess
import tarfile
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "run2_remote", Path(__file__).resolve().parent.parent / "scripts" / "run2_remote.py"
)
rr = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(rr)


class _Chan:
    def __init__(self, rc):
        self._rc = rc

    def recv_exit_status(self):
        return self._rc


class _Out:
    def __init__(self, data: bytes, rc: int):
        self._d, self.channel = data, _Chan(rc)

    def read(self):
        return self._d


class _Sftp:
    def putfo(self, fo, path):
        Path(path).write_bytes(fo.read())

    def open(self, path, mode):
        return open(path, "w")  # noqa: SIM115

    def chmod(self, path, mode):
        os.chmod(path, mode)

    def listdir_attr(self, path):
        class A:
            pass

        out = []
        for p in Path(path).iterdir():
            a = A()
            a.filename, a.st_mode = p.name, p.stat().st_mode
            out.append(a)
        return out

    def get(self, remote, local):
        Path(local).write_bytes(Path(remote).read_bytes())

    def close(self):
        pass


class _Transport:
    def is_active(self):
        return True


class FakeClient:
    def exec_command(self, cmd, timeout=None):
        p = subprocess.run(["bash", "-c", cmd], capture_output=True)
        return None, _Out(p.stdout + p.stderr, p.returncode), None

    def open_sftp(self):
        return _Sftp()

    def get_transport(self):
        return _Transport()

    def close(self):
        pass


def _fake_repo_tar() -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        body = (
            b'#!/usr/bin/env bash\necho "[setup] token len ${#HF_TOKEN}"\n'
            b"echo READY\nexit ${FAKE_RC:-0}\n"
        )
        ti = tarfile.TarInfo("wattnot/scripts/h100_vm_setup.sh")
        ti.size, ti.mode = len(body), 0o755
        t.addfile(ti, io.BytesIO(body))
    return buf.getvalue()


def _setup(tmp_path, monkeypatch, capsys, rc=0):
    monkeypatch.setattr(rr, "ROOT", str(tmp_path))
    monkeypatch.setattr(rr, "REMOTE_REPO", f"{tmp_path}/wattnot")
    monkeypatch.setattr(rr, "REMOTE_LOG", f"{tmp_path}/run2")
    monkeypatch.setattr(rr, "SETUP_LOG", f"{tmp_path}/run2/setup.log")
    monkeypatch.setattr(rr, "connect", lambda *a, **k: FakeClient())
    monkeypatch.setattr(rr, "archive", _fake_repo_tar)
    monkeypatch.setenv("HF_TOKEN", "hf_secret_token")
    monkeypatch.setenv("FAKE_RC", str(rc))
    args = rr.argparse.Namespace(host="x", key="k", timeout=30, model="m/m", moe="m/e")
    code = rr.cmd_setup(args)
    return code, capsys.readouterr().out


def test_setup_uploads_runs_follows_and_removes_the_token(tmp_path, monkeypatch, capsys):
    code, out = _setup(tmp_path, monkeypatch, capsys)
    assert code == 0
    assert "READY" in out and "token len 15" in out
    assert "hf_secret_token" not in out
    assert not (tmp_path / ".hf_token").exists()
    assert (tmp_path / "run2" / "setup.rc").read_text().strip() == "0"


def test_setup_reports_the_scripts_failure(tmp_path, monkeypatch, capsys):
    code, out = _setup(tmp_path, monkeypatch, capsys, rc=12)
    assert code == 2 and "exit code 12" in out


def test_setup_needs_a_token(monkeypatch):
    monkeypatch.delenv("HF_TOKEN", raising=False)
    args = rr.argparse.Namespace(host="x", key="k", timeout=1, model="m", moe="e")
    assert rr.cmd_setup(args) == 2


def test_pull_copies_the_log_directory(tmp_path, monkeypatch):
    log = tmp_path / "run2"
    (log / "sub").mkdir(parents=True)
    (log / "vllm.log").write_text("hello")
    (log / "sub" / "a.txt").write_text("x")
    monkeypatch.setattr(rr, "REMOTE_LOG", str(log))
    monkeypatch.setattr(rr, "connect", lambda *a, **k: FakeClient())
    out = tmp_path / "pulled"
    assert rr.cmd_pull(rr.argparse.Namespace(host="x", key="k", out=str(out))) == 0
    assert (out / "run2" / "vllm.log").read_text() == "hello"
    assert (out / "run2" / "sub" / "a.txt").exists()
    assert stat.S_ISDIR((out / "run2" / "sub").stat().st_mode)


def test_the_real_archive_has_the_setup_script_and_workload():
    names = tarfile.open(fileobj=io.BytesIO(rr.archive()), mode="r:gz").getnames()
    assert "wattnot/scripts/h100_vm_setup.sh" in names
    assert "wattnot/workloads/proof-v1.jsonl" in names
