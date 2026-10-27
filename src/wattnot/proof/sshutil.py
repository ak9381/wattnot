"""SSH helpers that cannot hang: every wait has a deadline.

RUN2's control session stalled for good at 20:18 while the GPU host kept working. Three things in
the first version let that happen:

* `Channel.recv_exit_status()` has no timeout, so a command whose peer went silent after sending
  its output blocked forever;
* paramiko's keepalive only *sends* a packet, it never decides the peer is dead, so a path that
  silently dropped packets was not noticed until the kernel gave up (on Linux, about 15 minutes);
* the poll loop's "broken read, ask again" branch skipped the timeout check, so it could spin.

`exec_bounded` waits for output and for the exit status against one deadline. `harden_socket`
turns on TCP keepalive and a TCP user timeout, so the kernel itself drops a dead path within about
a minute and the next call fails fast and reconnects. The commands we run are detached on the
GPU host, so a dropped session loses nothing.
"""

from __future__ import annotations

import os
import socket
import time

CONNECT_TIMEOUT_S = 20.0
KEEPALIVE_S = 15
TCP_IDLE_S = 15
TCP_INTERVAL_S = 5
TCP_PROBES = 4
TCP_USER_TIMEOUT_MS = 45_000


class SshDown(ConnectionError):
    """The GPU host could not be reached (or went quiet) for longer than the reconnect budget."""


def harden_socket(sock) -> list[str]:
    """TCP keepalive and user timeout on `sock` (best effort; returns what was applied)."""
    applied: list[str] = []
    if sock is None or not hasattr(sock, "setsockopt"):
        return applied
    opts = [("SO_KEEPALIVE", socket.SOL_SOCKET, "SO_KEEPALIVE", 1)]
    opts += [
        ("TCP_KEEPIDLE", socket.IPPROTO_TCP, "TCP_KEEPIDLE", TCP_IDLE_S),
        ("TCP_KEEPINTVL", socket.IPPROTO_TCP, "TCP_KEEPINTVL", TCP_INTERVAL_S),
        ("TCP_KEEPCNT", socket.IPPROTO_TCP, "TCP_KEEPCNT", TCP_PROBES),
        ("TCP_USER_TIMEOUT", socket.IPPROTO_TCP, "TCP_USER_TIMEOUT", TCP_USER_TIMEOUT_MS),
    ]
    for name, level, attr, val in opts:
        const = getattr(socket, attr, None)
        if const is None:  # not on every platform
            continue
        try:
            sock.setsockopt(level, const, val)
            applied.append(name)
        except OSError:
            pass
    return applied


def connect(host: str, key: str, *, user: str = "root", timeout: float = CONNECT_TIMEOUT_S):
    """A paramiko client with connect/banner/auth timeouts, keepalives and a hardened socket."""
    import paramiko

    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(
        host,
        username=user,
        key_filename=os.path.expanduser(key),
        timeout=timeout,
        banner_timeout=timeout,
        auth_timeout=timeout,
        allow_agent=False,
        look_for_keys=False,
    )
    tr = c.get_transport()
    tr.set_keepalive(KEEPALIVE_S)
    harden_socket(getattr(tr, "sock", None))
    return c


def exec_bounded(transport, cmd: str, timeout: float = 60.0) -> tuple[int, bytes]:
    """Run `cmd` on a paramiko Transport and return (exit status, output), within `timeout`
    seconds in all. Raises TimeoutError past that, and whatever paramiko raises for a dead link."""
    deadline = time.monotonic() + timeout
    ch = transport.open_session(timeout=min(timeout, CONNECT_TIMEOUT_S))
    try:
        ch.settimeout(min(timeout, 10.0))  # one read; the loop below owns the overall deadline
        ch.exec_command(cmd)
        out = bytearray()
        while True:
            if time.monotonic() > deadline:
                raise TimeoutError(f"no answer within {timeout:.0f}s")
            try:
                d = ch.recv(65536)
            except TimeoutError:
                continue
            if not d:
                break
            out += d
        while not ch.exit_status_ready():
            if time.monotonic() > deadline:
                raise TimeoutError(f"no exit status within {timeout:.0f}s")
            time.sleep(0.05)
        return ch.recv_exit_status(), bytes(out)
    finally:
        try:
            ch.close()
        except Exception:  # noqa: BLE001
            pass
