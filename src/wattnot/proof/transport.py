"""How the control machine runs a command on the GPU host and reads its output as it happens.

LocalTransport runs the command here (rehearsal, or the GPU host is this machine).
SshTransport runs it on the GPU host over SSH, detached (`setsid nohup`) with its output in a log
file there, and tails that file from a byte offset. If the connection drops, the command keeps
running and the next poll carries on from where the last one stopped, so no event is lost or
repeated. It needs `paramiko` (the control machine only; the GPU host needs nothing extra).

Hang safety (see sshutil): every call has a deadline, the TCP socket has keepalive and a user
timeout, and a read that keeps failing is retried on a fresh connection until a reconnect budget
runs out, then `SshDown` is raised so the run stops cleanly and can be resumed. A command started
with a `key` can be re-attached after the control process died: the same key and arguments find the
same log on the host, and the lines already consumed are replayed quietly (`skip`).
"""

from __future__ import annotations

import hashlib
import os
import shlex
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

from wattnot.proof import sshutil
from wattnot.proof.sshutil import SshDown

LineSink = Callable[[str], None]


class Transport:
    def run(
        self,
        args: list[str],
        on_line: LineSink,
        *,
        timeout_s: float = 7200,
        key: str | None = None,
        skip: int = 0,
        on_skip: LineSink | None = None,
        progress: Callable[[int], None] | None = None,
    ) -> int:
        """Run a GPU-host command; `on_line` gets each output line.

        `key` names the command so a later process can re-attach to it (Ssh only). The first
        `skip` lines go to `on_skip` instead (they were handled before); `progress(n)` reports the
        number of lines consumed so far."""
        ...
    def pull(self, remote: str, local: Path) -> None: ...
    def close(self) -> None: ...


class LocalTransport(Transport):
    def __init__(self, workdir: Path | None = None, env: dict | None = None) -> None:
        self.workdir, self.env = workdir, env

    def run(
        self,
        args: list[str],
        on_line: LineSink,
        *,
        timeout_s: float = 7200,
        key: str | None = None,
        skip: int = 0,
        on_skip: LineSink | None = None,
        progress: Callable[[int], None] | None = None,
    ) -> int:
        cmd = [sys.executable, "-m", "wattnot.proof.run", *args]
        p = subprocess.Popen(
            cmd,
            cwd=self.workdir,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env={**os.environ, **(self.env or {})},
        )
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


class SshTransport(Transport):
    def __init__(
        self,
        host: str,
        *,
        key: str = "~/.ssh/e2e_h100",
        user: str = "root",
        repo: str = "/root/wattnot",
        venv: str = "/root/venv",
        logdir: str = "/root/run2/cmdlogs",
        poll_s: float = 1.5,
        exec_timeout_s: float = 60.0,
        reconnect_budget_s: float = 600.0,
        stall_s: float = 240.0,
        connect_timeout_s: float = sshutil.CONNECT_TIMEOUT_S,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.host, self.key, self.user = host, os.path.expanduser(key), user
        self.repo, self.venv, self.logdir, self.poll_s = repo, venv, logdir, poll_s
        self.exec_timeout_s = exec_timeout_s  # one remote call, start to exit status
        self.reconnect_budget_s = reconnect_budget_s  # give up (SshDown) after this long of failing
        self.stall_s = stall_s  # no good poll for this long: drop the connection and redial
        self.connect_timeout_s = connect_timeout_s
        self._sleep = sleep
        self._c = None
        self._n = 0

    # -- connection ---------------------------------------------------------------------------
    def _dial(self):
        return sshutil.connect(
            self.host, self.key, user=self.user, timeout=self.connect_timeout_s
        )

    def _client(self):
        if self._c is not None and self._c.get_transport() and self._c.get_transport().is_active():
            return self._c
        self._drop()
        self._c = self._dial()
        return self._c

    def _drop(self) -> None:
        c, self._c = self._c, None
        if c is not None:
            try:
                c.close()
            except Exception:  # noqa: BLE001
                pass

    def _exec(self, cmd: str, timeout: float | None = None) -> tuple[int, bytes]:
        """One remote call; on any failure drop the connection, back off, redial and retry, until
        the reconnect budget is spent (then SshDown). Safe to repeat: the commands we send are
        reads, or idempotent starts."""
        timeout = self.exec_timeout_s if timeout is None else timeout
        t_fail: float | None = None
        attempt = 0
        last: Exception | None = None
        while True:
            try:
                return sshutil.exec_bounded(self._client().get_transport(), cmd, timeout)
            except Exception as e:  # noqa: BLE001
                last = e
                self._drop()
            now = time.monotonic()
            t_fail = now if t_fail is None else t_fail
            if now - t_fail > self.reconnect_budget_s:
                raise SshDown(
                    f"cannot reach {self.host} for {now - t_fail:.0f}s "
                    f"(last error: {type(last).__name__}: {str(last)[:120]})"
                ) from last
            self._sleep(min(30.0, 2.0**attempt))
            attempt += 1

    # -- commands -----------------------------------------------------------------------------
    def _tag(self, args: list[str], key: str | None) -> str:
        h = hashlib.sha256("\0".join(args).encode()).hexdigest()[:8]
        if key:
            return f"{''.join(c if c.isalnum() or c in '-_' else '-' for c in key)}-{h}"
        self._n += 1
        return f"{int(time.time())}-{self._n}-{h}"

    def _start_script(self, tag: str, args: list[str]) -> str:
        log, rcf, pidf = (f"{self.logdir}/{tag}.{x}" for x in ("log", "rc", "pid"))
        inner = (
            f"echo $$ > {pidf}; "
            f"cd {shlex.quote(self.repo)} && . {shlex.quote(self.venv)}/bin/activate && "
            f"python -m wattnot.proof.run {' '.join(shlex.quote(a) for a in args)}"
            f"; echo $? > {rcf}"
        )
        # Idempotent: a lost reply can be retried without starting a second copy. A copy that
        # already failed is set aside and run again; one that is running, or finished well, is
        # re-attached (its log is read again from the top).
        return (
            f"mkdir -p {self.logdir} && cd /root && "
            f"if [ -e {log} ]; then "
            f"  if [ -e {rcf} ] && [ \"$(cat {rcf})\" != 0 ]; then "
            f"    mv {log} {log}.failed-$(date +%s); rm -f {rcf} {pidf}; NEW=1; "
            f"  else NEW=0; fi; "
            f"else NEW=1; fi; "
            f"if [ $NEW = 1 ]; then "
            f"  (setsid nohup bash -c {shlex.quote(inner)} > {log} 2>&1 < /dev/null &); "
            f"  echo started; else echo attached; fi"
        )

    def run(
        self,
        args: list[str],
        on_line: LineSink,
        *,
        timeout_s: float = 7200,
        key: str | None = None,
        skip: int = 0,
        on_skip: LineSink | None = None,
        progress: Callable[[int], None] | None = None,
    ) -> int:
        tag = self._tag(args, key)
        log, rcf, pidf = (f"{self.logdir}/{tag}.{x}" for x in ("log", "rc", "pid"))
        rc, out = self._exec(self._start_script(tag, args))
        if rc != 0 or not (b"started" in out or b"attached" in out):
            raise RuntimeError(f"could not start the command: {out[-200:]!r}")
        attached = b"attached" in out
        if not attached:
            skip = 0  # a fresh copy: nothing in its log was handled yet
        offset, buf, n_lines, t0 = 0, b"", 0, time.monotonic()
        t_good = time.monotonic()
        dead = 0
        poll = (
            f"tail -c +{{off}} {log} 2>/dev/null; "
            f"printf '\n__END__%s' \"$(cat {rcf} 2>/dev/null)\"; "
            f"printf '\n__ALIVE__%s' \"$([ -e {pidf} ] && "
            f"(kill -0 $(cat {pidf}) 2>/dev/null && echo 1 || echo 0) || echo x)\""
        )

        def emit(line: bytes) -> None:
            nonlocal n_lines
            n_lines += 1
            text = line.decode(errors="replace")
            handler = (on_skip or on_line) if n_lines <= skip else on_line
            handler(text)

        while True:
            if time.monotonic() - t0 > timeout_s:
                try:
                    self._exec(f"kill -TERM -$(cat {pidf}) 2>/dev/null; "
                               "pkill -f 'wattnot.proof.run' || true", timeout=20)
                except Exception:  # noqa: BLE001 - best effort; we are giving up anyway
                    pass
                return 124
            try:
                _, chunk = self._exec(poll.format(off=offset + 1))
            except SshDown:
                raise
            body, sep, rest = chunk.rpartition(b"\n__END__")
            if not sep:  # a broken read: ask again from the same offset, but not for ever
                if time.monotonic() - t_good > self.stall_s:
                    self._drop()  # redial; _exec's budget decides when to give up
                    t_good = time.monotonic()
                self._sleep(self.poll_s)
                continue
            t_good = time.monotonic()
            rc_s, _, alive = rest.partition(b"\n__ALIVE__")
            offset += len(body)
            buf += body
            *lines, buf = buf.split(b"\n")
            for ln in lines:
                emit(ln)
            if progress:
                progress(n_lines)
            if rc_s.strip():
                if buf:
                    emit(buf)
                    if progress:
                        progress(n_lines)
                return int(rc_s.strip())
            # no exit code yet: if the process is gone and left none, it was killed (reboot, OOM)
            dead = dead + 1 if alive.strip() == b"0" else 0
            if dead >= 3:
                if buf:
                    emit(buf)
                return 125
            self._sleep(self.poll_s)

    def pull(self, remote: str, local: Path) -> None:
        import stat

        sftp = self._client().open_sftp()
        sftp.get_channel().settimeout(60)  # a stalled transfer raises instead of hanging
        local.mkdir(parents=True, exist_ok=True)

        def walk(r: str, l_: Path) -> None:
            l_.mkdir(parents=True, exist_ok=True)
            for e in sftp.listdir_attr(r):
                if stat.S_ISDIR(e.st_mode):
                    walk(f"{r}/{e.filename}", l_ / e.filename)
                else:
                    sftp.get(f"{r}/{e.filename}", str(l_ / e.filename))

        try:
            walk(remote, local)
        except Exception:
            self._drop()  # the next call redials
            raise
        finally:
            try:
                sftp.close()
            except Exception:  # noqa: BLE001
                pass

    def close(self) -> None:
        self._drop()
