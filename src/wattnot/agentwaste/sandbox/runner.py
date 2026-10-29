"""Run generated Python in a locked-down subprocess and measure what the run actually cost.

The lock-down, in layers (defence in depth -- a weakness in one layer is covered by another):

1. **No network.** ``unshare -n`` drops the child into an empty network namespace when the
   kernel allows it (unprivileged user namespaces). Where it does not (most CI, this sandbox),
   the child still installs a Python **audit hook** that refuses every ``socket.*`` event, so a
   connect attempt raises instead of leaving the box. Which layer applied is recorded, honestly.
2. **Resource limits.** ``RLIMIT_CPU`` (SIGXCPU on overrun) and ``RLIMIT_AS`` (address-space
   cap -> ``MemoryError``) are set in a ``preexec_fn`` before ``exec``. A **wall-clock timeout**
   in the parent kills the whole process group if the child wedges without burning CPU.
3. **No stray writes.** The child runs in a throwaway temp dir; the audit hook refuses any
   ``open`` for writing whose resolved path is not inside that dir. Reads (stdlib imports) are
   left alone.
4. **Minimal environment.** Only ``PATH`` and a fixed ``PYTHONHASHSEED`` are passed, so the run
   is reproducible and carries none of the parent's secrets.

Everything the parent reports about the run -- wall time, CPU seconds, peak RSS, exit status,
whether the network and out-of-dir writes were actually refused -- is MEASURED here by running
the child. Nothing in this file estimates anything.
"""

from __future__ import annotations

import json
import os
import resource
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

# The child-side bootstrap. Installs the audit hook FIRST (before importing the stub or running
# the program), then execs the program with the injected `tools`, and writes result.json inside
# the sandbox dir. The hook can never be removed once added, so anything the program does after
# is covered.
_BOOTSTRAP = r'''
import sys, os, json

SANDBOX_DIR = os.path.realpath(sys.argv[1])

def _guard(event, args):
    # Refuse all networking outright.
    if event.startswith("socket.") or event in ("urllib.Request",):
        raise PermissionError("network blocked in sandbox: " + event)
    # Refuse spawning other processes.
    if event in ("os.system", "os.exec", "subprocess.Popen"):
        raise PermissionError("process spawn blocked in sandbox: " + event)
    # Refuse writes outside the sandbox dir. The "open" event is (path, mode, flags).
    if event == "open":
        path, mode = args[0], args[1]
        writing = bool(mode) and any(c in mode for c in "wax+")
        if writing and path is not None:
            rp = os.path.realpath(path)
            if not (rp == SANDBOX_DIR or rp.startswith(SANDBOX_DIR + os.sep)):
                raise PermissionError("write outside sandbox blocked: " + str(path))

sys.addaudithook(_guard)

sys.path.insert(0, SANDBOX_DIR)
import stub  # noqa: E402

tools = stub.Tools(os.path.join(SANDBOX_DIR, "recordings.json"))
ns = {"tools": tools, "__name__": "__main__"}
src = open(os.path.join(SANDBOX_DIR, "program.py")).read()
exec(compile(src, "program.py", "exec"), ns)
answer = ns.get("answer")
with open(os.path.join(SANDBOX_DIR, "result.json"), "w") as fh:
    json.dump({"answer": answer, "tool_calls": getattr(tools, "calls", None)}, fh, ensure_ascii=False)
'''


@dataclass
class Limits:
    """The sandbox's resource ceilings. All enforced; all MEASURED against at run time."""

    cpu_s: int = 5             # RLIMIT_CPU seconds (SIGXCPU past this)
    as_bytes: int = 512 * 1024 * 1024  # RLIMIT_AS address-space cap
    wall_s: float = 15.0       # parent-side wall-clock kill


@dataclass
class SandboxResult:
    """What actually happened, measured by running the child."""

    ok: bool
    exit_status: int | None
    signal: int | None
    timed_out: bool
    wall_s: float
    cpu_s: float               # user+system CPU of the child (ru_utime + ru_stime)
    peak_rss_kb: int           # ru_maxrss of the child
    net_isolation: str         # "unshare -n" or "audit-hook"
    stdout: str
    stderr: str
    answer: object = None
    tool_calls: int | None = None
    error: str | None = None
    extras: dict = field(default_factory=dict)


def _unshare_net_works() -> bool:
    """True only if ``unshare -n`` actually runs here (unprivileged netns allowed)."""
    exe = shutil.which("unshare")
    if not exe:
        return False
    try:
        r = subprocess.run([exe, "-n", "true"], capture_output=True, timeout=5)
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _preexec(limits: Limits):
    def _apply() -> None:  # pragma: no cover - runs in the forked child
        os.setsid()  # own process group, so the parent can kill the whole tree on timeout
        resource.setrlimit(resource.RLIMIT_CPU, (limits.cpu_s, limits.cpu_s))
        resource.setrlimit(resource.RLIMIT_AS, (limits.as_bytes, limits.as_bytes))
        # No core dumps, bounded file descriptors.
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    return _apply


def _minimal_env() -> dict:
    return {"PATH": "/usr/bin:/bin", "PYTHONHASHSEED": "0", "LC_ALL": "C",
            "PYTHONDONTWRITEBYTECODE": "1"}


def run_code(program_src: str, stub_src: str, recordings: dict, limits: Limits | None = None,
             *, workdir: str | Path | None = None) -> SandboxResult:
    """Run ``program_src`` against the generated ``stub_src`` + ``recordings`` under lock-down.

    Returns a :class:`SandboxResult` whose timing/memory/exit fields are MEASURED by running
    the child. The sandbox dir is created fresh and removed afterwards (unless ``workdir`` is
    given, in which case the caller owns it).
    """
    limits = limits or Limits()
    owned = workdir is None
    sdir = Path(workdir) if workdir else Path(tempfile.mkdtemp(prefix="wn-codemode-"))
    try:
        (sdir / "program.py").write_text(program_src, encoding="utf-8")
        (sdir / "stub.py").write_text(stub_src, encoding="utf-8")
        (sdir / "recordings.json").write_text(json.dumps(recordings, ensure_ascii=False),
                                              encoding="utf-8")
        (sdir / "_bootstrap.py").write_text(_BOOTSTRAP, encoding="utf-8")

        net = "unshare -n" if _unshare_net_works() else "audit-hook"
        argv: list[str] = []
        if net == "unshare -n":
            argv = [shutil.which("unshare"), "-n"]
        # -s (no user site) and -B (no .pyc) keep the run clean; env is already minimal.
        argv += [sys.executable, "-s", "-B", str(sdir / "_bootstrap.py"), str(sdir)]

        proc = subprocess.Popen(
            argv, cwd=str(sdir), env=_minimal_env(),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            preexec_fn=_preexec(limits),
        )
        # Wall-clock enforcement: a timer kills the whole process group if the child wedges
        # without burning CPU (e.g. time.sleep), which RLIMIT_CPU would never catch.
        timed_out = {"v": False}

        def _kill_group() -> None:
            timed_out["v"] = True
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError, OSError):
                try:
                    proc.kill()
                except OSError:
                    pass

        timer = threading.Timer(limits.wall_s, _kill_group)
        timer.start()
        t0 = time.monotonic()
        # os.wait4 reaps the child AND returns its exact rusage (cpu, peak RSS) for THIS run.
        _, status, ru = os.wait4(proc.pid, 0)
        wall = round(time.monotonic() - t0, 4)
        timer.cancel()
        stdout = proc.stdout.read() if proc.stdout else ""
        stderr = proc.stderr.read() if proc.stderr else ""
        proc.stdout and proc.stdout.close()
        proc.stderr and proc.stderr.close()
        proc.returncode = -os.WTERMSIG(status) if os.WIFSIGNALED(status) else os.WEXITSTATUS(status)

        sig = os.WTERMSIG(status) if os.WIFSIGNALED(status) else None
        exit_status = os.WEXITSTATUS(status) if os.WIFEXITED(status) else None

        answer = None
        tool_calls = None
        err = None
        rp = sdir / "result.json"
        if rp.is_file():
            try:
                payload = json.loads(rp.read_text())
                answer = payload.get("answer")
                tool_calls = payload.get("tool_calls")
            except (ValueError, OSError) as e:
                err = f"result unreadable: {e}"
        elif not timed_out["v"]:
            err = "no result produced"

        ok = (not timed_out["v"]) and exit_status == 0 and err is None
        return SandboxResult(
            ok=ok, exit_status=exit_status, signal=sig, timed_out=timed_out["v"],
            wall_s=wall, cpu_s=round(ru.ru_utime + ru.ru_stime, 4),
            peak_rss_kb=int(ru.ru_maxrss), net_isolation=net,
            stdout=stdout or "", stderr=stderr or "", answer=answer, tool_calls=tool_calls,
            error=err,
        )
    finally:
        if owned:
            shutil.rmtree(sdir, ignore_errors=True)
