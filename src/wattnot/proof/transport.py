"""How the control machine runs a command on the GPU host and reads its output as it happens.

LocalTransport runs the command here (rehearsal, or the GPU host is this machine).
SshTransport runs it on the GPU host over SSH, detached (`setsid nohup`) with its output in a log
file there, and tails that file from a byte offset. If the connection drops, the command keeps
running and the next poll carries on from where the last one stopped, so no event is lost or
repeated. It needs `paramiko` (the control machine only; the GPU host needs nothing extra).
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

LineSink = Callable[[str], None]


class Transport:
    def run(self, args: list[str], on_line: LineSink, *, timeout_s: float = 7200) -> int: ...
    def pull(self, remote: str, local: Path) -> None: ...
    def close(self) -> None: ...


class LocalTransport(Transport):
    def __init__(self, workdir: Path | None = None, env: dict | None = None) -> None:
        self.workdir, self.env = workdir, env

    def run(self, args: list[str], on_line: LineSink, *, timeout_s: float = 7200) -> int:
        cmd = [sys.executable, "-m", "wattnot.proof.run", *args]
        p = subprocess.Popen(cmd, cwd=self.workdir, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True, bufsize=1,
                             env={**os.environ, **(self.env or {})})
        t0 = time.time()
        assert p.stdout is not None
        for line in p.stdout:
            on_line(line.rstrip("\n"))
            if time.time() - t0 > timeout_s:
                p.kill()
                break
        return p.wait()

    def pull(self, remote: str, local: Path) -> None:
        if Path(remote).resolve() != Path(local).resolve():
            import shutil
            shutil.copytree(remote, local, dirs_exist_ok=True)

    def close(self) -> None:
        return None


class SshTransport(Transport):  # pragma: no cover - needs a real host
    def __init__(self, host: str, *, key: str = "~/.ssh/e2e_h100", user: str = "root",
                 repo: str = "/root/wattnot", venv: str = "/root/venv",
                 logdir: str = "/root/run2/cmdlogs", poll_s: float = 1.5) -> None:
        self.host, self.key, self.user = host, os.path.expanduser(key), user
        self.repo, self.venv, self.logdir, self.poll_s = repo, venv, logdir, poll_s
        self._c = None
        self._n = 0

    def _client(self):
        import paramiko
        if self._c is not None and self._c.get_transport() and self._c.get_transport().is_active():
            return self._c
        c = paramiko.SSHClient()
        c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        c.connect(self.host, username=self.user, key_filename=self.key, timeout=20,
                  banner_timeout=20, auth_timeout=20, allow_agent=False, look_for_keys=False)
        c.get_transport().set_keepalive(15)
        self._c = c
        return c

    def _exec(self, cmd: str, timeout: float = 60) -> tuple[int, bytes]:
        for attempt in range(6):
            try:
                ch = self._client().get_transport().open_session()
                ch.settimeout(timeout)
                ch.exec_command(cmd)
                out = b""
                while True:
                    d = ch.recv(65536)
                    if not d:
                        break
                    out += d
                return ch.recv_exit_status(), out
            except Exception:  # noqa: BLE001 - reconnect and retry; the command is detached
                self._c = None
                time.sleep(min(30, 2 ** attempt))
        raise ConnectionError(f"cannot reach {self.host}")

    def run(self, args: list[str], on_line: LineSink, *, timeout_s: float = 7200) -> int:
        self._n += 1
        tag = f"{int(time.time())}-{self._n}"
        log, rcf = f"{self.logdir}/{tag}.log", f"{self.logdir}/{tag}.rc"
        inner = (f"cd {shlex.quote(self.repo)} && . {shlex.quote(self.venv)}/bin/activate && "
                 f"python -m wattnot.proof.run {' '.join(shlex.quote(a) for a in args)}"
                 f"; echo $? > {rcf}")
        wrapped = (f"mkdir -p {self.logdir} && cd /root && (setsid nohup bash -c "
                   f"{shlex.quote(inner)} > {log} 2>&1 < /dev/null &) ; echo started")
        rc, out = self._exec(wrapped)
        if rc != 0 or b"started" not in out:
            raise RuntimeError(f"could not start the command: {out[-200:]!r}")
        offset, buf, t0 = 0, b"", time.time()
        poll = (f"tail -c +{{off}} {log} 2>/dev/null; "
                f"printf '\n__END__%s' \"$(cat {rcf} 2>/dev/null)\"")
        while True:
            _, chunk = self._exec(poll.format(off=offset + 1))
            body, sep, rc_s = chunk.rpartition(b"\n__END__")
            if not sep:  # a broken read: ask again from the same offset
                time.sleep(self.poll_s)
                continue
            offset += len(body)
            buf += body
            *lines, buf = buf.split(b"\n")
            for ln in lines:
                on_line(ln.decode(errors="replace"))
            if rc_s.strip():
                if buf:
                    on_line(buf.decode(errors="replace"))
                return int(rc_s.strip())
            if time.time() - t0 > timeout_s:
                self._exec("pkill -f 'wattnot.proof.run' || true")
                return 124
            time.sleep(self.poll_s)

    def pull(self, remote: str, local: Path) -> None:
        import stat
        sftp = self._client().open_sftp()
        local.mkdir(parents=True, exist_ok=True)

        def walk(r: str, l_: Path) -> None:
            l_.mkdir(parents=True, exist_ok=True)
            for e in sftp.listdir_attr(r):
                if stat.S_ISDIR(e.st_mode):
                    walk(f"{r}/{e.filename}", l_ / e.filename)
                else:
                    sftp.get(f"{r}/{e.filename}", str(l_ / e.filename))
        walk(remote, local)
        sftp.close()

    def close(self) -> None:
        if self._c is not None:
            self._c.close()
            self._c = None
