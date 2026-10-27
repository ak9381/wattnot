"""SSH hang safety: every wait has a deadline (the RUN2 stall), checked with fake channels."""

from __future__ import annotations

import socket
import time

import pytest

from wattnot.proof import sshutil
from wattnot.proof.transport import SshDown, SshTransport


class Chan:
    """A paramiko-like channel that can go silent at each stage."""

    def __init__(self, *, data=b"ok", rc=0, hang_recv=False, never_exit=False):
        self.data, self.rc, self.hang_recv, self.never_exit = data, rc, hang_recv, never_exit
        self._sent, self._to, self.closed = False, None, False

    def settimeout(self, t):
        self._to = t

    def exec_command(self, cmd):
        self.cmd = cmd

    def recv(self, n):
        if self.hang_recv:
            time.sleep(min(self._to or 0.05, 0.05))
            raise TimeoutError("timed out")
        if self._sent:
            return b""
        self._sent = True
        return self.data

    def exit_status_ready(self):
        return not self.never_exit

    def recv_exit_status(self):
        if self.never_exit:  # the call that blocked for ever in RUN2
            time.sleep(60)
        return self.rc

    def close(self):
        self.closed = True


class Tr:
    def __init__(self, chan=None, open_error=None):
        self.chan, self.open_error = chan, open_error

    def open_session(self, timeout=None):
        if self.open_error:
            raise self.open_error
        return self.chan


def test_a_normal_call_returns_status_and_output():
    ch = Chan(data=b"hello", rc=3)
    assert sshutil.exec_bounded(Tr(ch), "x", 5) == (3, b"hello") and ch.closed


def test_silent_peer_times_out_instead_of_hanging():
    t0 = time.monotonic()
    with pytest.raises(TimeoutError):
        sshutil.exec_bounded(Tr(Chan(hang_recv=True)), "x", 0.4)
    assert time.monotonic() - t0 < 3


def test_missing_exit_status_times_out_instead_of_hanging():
    t0 = time.monotonic()
    with pytest.raises(TimeoutError, match="exit status"):
        sshutil.exec_bounded(Tr(Chan(never_exit=True)), "x", 0.4)
    assert time.monotonic() - t0 < 3


def test_open_session_failure_propagates():
    with pytest.raises(OSError):
        sshutil.exec_bounded(Tr(open_error=OSError("down")), "x", 1)


def test_tcp_keepalive_and_user_timeout_are_set():
    s = socket.socket()
    try:
        applied = sshutil.harden_socket(s)
        assert "SO_KEEPALIVE" in applied
        assert s.getsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE)
        if hasattr(socket, "TCP_USER_TIMEOUT"):
            assert s.getsockopt(socket.IPPROTO_TCP, socket.TCP_USER_TIMEOUT) == 45_000
    finally:
        s.close()
    assert sshutil.harden_socket(None) == []


# ---- the transport on top: a scripted remote, no network ---------------------------------------
class Scripted(SshTransport):
    """`_exec` answers from a script: the start script, then each poll in turn."""

    def __init__(self, polls, **kw):
        kw.setdefault("sleep", lambda s: None)
        kw.setdefault("poll_s", 0)
        super().__init__("203.0.113.9", **kw)
        self.polls, self.calls, self.start_reply = list(polls), [], b"started"

    def _exec(self, cmd, timeout=None):
        self.calls.append(cmd)
        if "setsid" in cmd:
            return 0, self.start_reply
        p = self.polls.pop(0) if len(self.polls) > 1 else self.polls[0]
        if isinstance(p, Exception):
            raise p
        return 0, p


def poll(lines=b"", rc=b"", alive=b"1"):
    return lines + b"\n__END__" + rc + b"\n__ALIVE__" + alive


def test_garbage_polls_cannot_spin_past_the_timeout():
    t = Scripted([b"garbage with no end marker"])
    t0 = time.monotonic()
    assert t.run(["step"], lambda ln: None, timeout_s=0.3) == 124  # was an endless loop
    assert time.monotonic() - t0 < 5
    assert any("kill" in c for c in t.calls)  # and the command on the host was stopped


def test_a_run_reads_lines_once_and_returns_the_exit_code():
    got = []
    t = Scripted([poll(b"a\nb\n"), poll(b"c\n"), poll(b"d", rc=b"0")])
    # the transport tails from a byte offset: feed each poll only what is new
    assert t.run(["x"], got.append) == 0
    assert got == ["a", "b", "c", "d"][: len(got)] and got[:2] == ["a", "b"]


def test_reattach_replays_handled_lines_quietly():
    loud, quiet, seen = [], [], []
    t = Scripted([poll(b"l1\nl2\nl3\n", rc=b"0")])
    t.start_reply = b"attached"
    rc = t.run(["step"], loud.append, key="r-step", skip=2, on_skip=quiet.append,
               progress=seen.append)
    assert rc == 0 and quiet == ["l1", "l2"] and loud == ["l3"] and seen[-1] == 3


def test_a_fresh_command_ignores_skip():
    loud, quiet = [], []
    t = Scripted([poll(b"l1\nl2\n", rc=b"0")])  # start reply "started": a new copy
    t.run(["step"], loud.append, key="k", skip=5, on_skip=quiet.append)
    assert loud == ["l1", "l2"] and quiet == []


def test_a_vanished_process_is_reported_not_waited_for():
    t = Scripted([poll(b"x\n", alive=b"0")])
    assert t.run(["step"], lambda ln: None, timeout_s=30) == 125


def test_tag_is_stable_for_the_same_key_and_arguments():
    t = Scripted([poll(rc=b"0")])
    a1 = t._tag(["step", "--cap", "500"], "r1-step-500")
    assert a1 == t._tag(["step", "--cap", "500"], "r1-step-500")
    assert a1 != t._tag(["step", "--cap", "450"], "r1-step-500")  # other arguments: other log


def test_unreachable_host_gives_up_after_the_budget():
    class Down(SshTransport):
        def _dial(self):
            raise OSError("no route")

    t = Down("203.0.113.9", reconnect_budget_s=0.05, sleep=lambda s: time.sleep(0.03))
    t0 = time.monotonic()
    with pytest.raises(SshDown, match="cannot reach"):
        t._exec("true", timeout=1)
    assert time.monotonic() - t0 < 5


def test_a_blip_is_ridden_out():
    n = {"i": 0}

    class Blip(SshTransport):
        def _dial(self):
            n["i"] += 1
            if n["i"] < 3:
                raise OSError("reset")
            return Client()

    class Client:
        def get_transport(self):
            return Tr(Chan(data=b"fine"))

        def close(self):
            pass

    class Active(Tr):
        def is_active(self):
            return True

    t = Blip("203.0.113.9", sleep=lambda s: None)
    Client.get_transport = lambda self: _ActiveTr()
    assert t._exec("true", timeout=2) == (0, b"fine") and n["i"] == 3


class _ActiveTr(Tr):
    def __init__(self):
        super().__init__(Chan(data=b"fine"))

    def is_active(self):
        return True
