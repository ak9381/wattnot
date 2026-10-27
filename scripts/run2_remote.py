#!/usr/bin/env python3
"""Control-machine helper for RUN2: get the code onto the GPU host, set it up, pull logs back.

    python scripts/run2_remote.py wait   --host IP [--timeout 900]   until SSH answers
    python scripts/run2_remote.py setup  --host IP                    upload repo, run setup script
    python scripts/run2_remote.py status --host IP                    GPU, server, disk, last logs
    python scripts/run2_remote.py pull   --host IP [--out DIR]        copy /root/run2 + logs back

`setup` ships `git archive HEAD` of this repo to /root/wattnot (the path SshTransport expects),
writes the Hugging Face token to a 0600 file on the host (never on a command line, never in a
log), starts the setup script detached, and follows its log until READY, a failure, or the
timeout. Exit codes: 0 ok; 2 setup failed (the script's own code is printed); 3 timeout.
Needs `paramiko`. Reads HF_TOKEN from the environment.
"""

from __future__ import annotations

import argparse
import io
import os
import shlex
import socket
import stat
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
KEY = "~/.ssh/e2e_h100"
ROOT = "/root"
REMOTE_REPO = f"{ROOT}/wattnot"
REMOTE_LOG = f"{ROOT}/run2"
SETUP_LOG = f"{REMOTE_LOG}/setup.log"


def connect(host: str, key: str = KEY, timeout: float = 20):
    import paramiko

    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(
        host,
        username="root",
        key_filename=os.path.expanduser(key),
        timeout=timeout,
        banner_timeout=timeout,
        auth_timeout=timeout,
        allow_agent=False,
        look_for_keys=False,
    )
    c.get_transport().set_keepalive(15)
    return c


def sh(c, cmd: str, timeout: float = 60) -> tuple[int, str]:
    _, out, _ = c.exec_command(cmd, timeout=timeout)
    data = out.read().decode(errors="replace")
    return out.channel.recv_exit_status(), data


def reconnecting(host: str, key: str):
    """A command runner that reconnects when the link drops (the remote work is detached)."""
    state = {"c": None}

    def run(cmd: str, timeout: float = 60) -> tuple[int, str]:
        for attempt in range(8):
            try:
                if state["c"] is None or not state["c"].get_transport().is_active():
                    state["c"] = connect(host, key)
                return sh(state["c"], cmd, timeout)
            except Exception:  # noqa: BLE001
                state["c"] = None
                time.sleep(min(30, 2**attempt))
        raise ConnectionError(f"cannot reach {host}")

    run.state = state  # type: ignore[attr-defined]
    return run


def cmd_wait(a) -> int:
    end = time.time() + a.timeout
    while time.time() < end:
        try:
            with socket.create_connection((a.host, 22), timeout=5):
                pass
            c = connect(a.host, a.key)
            rc, out = sh(c, "nvidia-smi --query-gpu=name,driver_version --format=csv,noheader")
            c.close()
            print(f"ssh ok; gpu: {out.strip() or '(nvidia-smi gave nothing)'}")
            return 0
        except Exception as e:  # noqa: BLE001
            print(f"[wait] {type(e).__name__}: {str(e)[:80]}", flush=True)
            time.sleep(10)
    print("timed out waiting for SSH", file=sys.stderr)
    return 3


def archive() -> bytes:
    return subprocess.run(
        ["git", "-C", str(REPO), "archive", "--format=tar.gz", "--prefix=wattnot/", "HEAD"],
        check=True,
        capture_output=True,
    ).stdout


def cmd_setup(a) -> int:
    token = os.environ.get("HF_TOKEN", "")
    if not token:
        print("HF_TOKEN is not set: the gated Llama download would fail", file=sys.stderr)
        return 2
    c = connect(a.host, a.key)
    rc, out = sh(c, f"mkdir -p {REMOTE_LOG} && rm -rf {REMOTE_REPO} && echo ok")
    assert rc == 0, out
    data = archive()
    rev = subprocess.run(
        ["git", "-C", str(REPO), "rev-parse", "--short", "HEAD"], capture_output=True, text=True
    ).stdout.strip()
    print(f"[run2_remote] uploading repo archive ({len(data) / 1e6:.1f} MB, commit {rev})")
    sftp = c.open_sftp()
    sftp.putfo(io.BytesIO(data), f"{ROOT}/wattnot.tgz")
    f = sftp.open(f"{ROOT}/.hf_token", "w")
    f.write(token)
    f.close()
    sftp.chmod(f"{ROOT}/.hf_token", 0o600)
    sftp.close()
    rc, out = sh(c, f"cd {ROOT} && tar xzf wattnot.tgz && ls wattnot/scripts/h100_vm_setup.sh")
    if rc != 0:
        print("unpack failed:", out, file=sys.stderr)
        return 2
    inner = (
        f"cd {REMOTE_REPO} && export HF_TOKEN=$(cat {ROOT}/.hf_token) && "
        f"bash scripts/h100_vm_setup.sh {shlex.quote(a.model)} {shlex.quote(a.moe)}; "
        f"echo $? > {REMOTE_LOG}/setup.rc"
    )
    rc, out = sh(
        c,
        f"rm -f {REMOTE_LOG}/setup.rc; cd {ROOT} && (setsid nohup bash -c {shlex.quote(inner)} "
        f"> {SETUP_LOG} 2>&1 < /dev/null &); echo started",
    )
    assert "started" in out, out
    c.close()
    run = reconnecting(a.host, a.key)
    offset, t0, buf = 0, time.time(), b""
    while True:
        rc, chunk = run(f"tail -c +{offset + 1} {SETUP_LOG} 2>/dev/null; "
                        f"printf '\\n__RC__%s' \"$(cat {REMOTE_LOG}/setup.rc 2>/dev/null)\"")
        body, sep, code = chunk.rpartition("\n__RC__")
        if sep:
            raw = body.encode()
            offset += len(raw)
            buf += raw
            *lines, buf = buf.split(b"\n")
            for ln in lines:
                print("[vm]", ln.decode(errors="replace")[:240], flush=True)
            if code.strip():
                if buf:
                    print("[vm]", buf.decode(errors="replace")[:240])
                rcode = int(code.strip())
                run(f"rm -f {ROOT}/.hf_token")  # the token is no longer needed on the host
                print(f"[run2_remote] setup exit code {rcode} after {time.time() - t0:.0f} s")
                return 0 if rcode == 0 else 2
        if time.time() - t0 > a.timeout:
            print("[run2_remote] setup timed out; the host keeps working, check `status`",
                  file=sys.stderr)
            return 3
        time.sleep(5)


def cmd_status(a) -> int:
    run = reconnecting(a.host, a.key)
    _, out = run(
        "nvidia-smi --query-gpu=name,power.limit,power.draw,temperature.gpu,memory.used "
        "--format=csv,noheader; df -h / | tail -n 1; "
        "curl -s -o /dev/null -w 'vllm /v1/models: %{http_code}\\n' http://127.0.0.1:8000/v1/models;"
        f" tail -n 5 {SETUP_LOG} 2>/dev/null; cat {REMOTE_LOG}/setup.rc 2>/dev/null"
    )
    print(out)
    return 0


def cmd_pull(a) -> int:
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    c = connect(a.host, a.key)
    sh(
        c,
        f"nvidia-smi -q -d POWER,CLOCK,TEMPERATURE,PERFORMANCE > {REMOTE_LOG}/nvsmi_power_end.txt "
        f"2>&1; nvidia-smi --query-gpu=power.limit --format=csv,noheader "
        f"> {REMOTE_LOG}/cap_at_end.txt 2>&1; true",
    )
    sftp = c.open_sftp()
    n = 0

    def walk(r: str, loc: Path) -> None:
        nonlocal n
        loc.mkdir(parents=True, exist_ok=True)
        try:
            entries = sftp.listdir_attr(r)
        except OSError:
            return
        for e in entries:
            if stat.S_ISDIR(e.st_mode):
                walk(f"{r}/{e.filename}", loc / e.filename)
            else:
                try:
                    sftp.get(f"{r}/{e.filename}", str(loc / e.filename))
                    n += 1
                except OSError as ex:
                    print(f"[pull] skipped {r}/{e.filename}: {ex}", file=sys.stderr)

    walk(REMOTE_LOG, out / "run2")
    sftp.close()
    c.close()
    print(f"[run2_remote] pulled {n} files to {out}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("wait", "setup", "status", "pull"):
        p = sub.add_parser(name)
        p.add_argument(
            "--host", default=os.environ.get("VM_IP"), required=not os.environ.get("VM_IP")
        )
        p.add_argument("--key", default=KEY)
        if name in ("wait", "setup"):
            p.add_argument("--timeout", type=float, default=900 if name == "wait" else 1800)
        if name == "setup":
            p.add_argument("--model", default="meta-llama/Llama-3.1-8B-Instruct")
            p.add_argument("--moe", default="ibm-granite/granite-3.1-3b-a800m-instruct")
        if name == "pull":
            p.add_argument("--out", default="run2-pulled")
    a = ap.parse_args(argv)
    return {"wait": cmd_wait, "setup": cmd_setup, "status": cmd_status, "pull": cmd_pull}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
