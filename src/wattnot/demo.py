"""``wattnot demo``: open the dashboard on the bundled replay.

Nothing runs live. The demo serves the recorded H100 run that ships in the
repo (``data/dash/h100-replay.json``): no GPU is touched, no network call is
made, no Meta/Baseten/Modal key is read, and no money is spent. It is the
fastest way for a hackathon judge to see WattNot: install, then run

    wattnot demo

The command picks a free port if the default is busy, prints a short banner
explaining what the judge is looking at, and opens a browser. Every path is
built with :mod:`pathlib`, so it works the same on Windows, macOS and Linux.
"""

from __future__ import annotations

import argparse
import socket
import sys
import threading
import webbrowser
from dataclasses import dataclass
from pathlib import Path

MIN_PYTHON = (3, 11)
DEFAULT_PORT = 8765
DEFAULT_HOST = "127.0.0.1"


def python_ok(version: tuple[int, ...] | None = None) -> bool:
    """True when the running interpreter is new enough for WattNot."""
    v = tuple((version or sys.version_info)[:2])
    return v >= MIN_PYTHON


def bundle_path() -> Path:
    """Absolute path to the bundled replay the demo serves.

    Reuses the dashboard server's constant so there is one source of truth.
    """
    from wattnot.dash.server import DEFAULT_BUNDLE

    return Path(DEFAULT_BUNDLE)


def find_free_port(preferred: int = DEFAULT_PORT, host: str = DEFAULT_HOST) -> int:
    """Return ``preferred`` if it can be bound, else an OS-picked free port.

    Binding briefly is the only portable way to learn a port is free across
    Windows, macOS and Linux. If even the OS cannot hand one out we fall back
    to ``preferred`` and let the server raise the real bind error.
    """
    for candidate in (preferred, 0):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind((host, candidate))
                return s.getsockname()[1]
            except OSError:
                continue
    return preferred


@dataclass
class DemoPlan:
    """Everything the demo needs, worked out before anything is started."""

    host: str
    port: int
    url: str
    bundle: Path
    open_browser: bool
    port_changed: bool
    requested_port: int


def prepare(
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    *,
    open_browser: bool = True,
    bundle: Path | None = None,
) -> DemoPlan:
    """Plan the demo run without starting a server.

    Raises :class:`FileNotFoundError` with a friendly message when the replay
    data is missing (for example, if run from outside a repo checkout).
    """
    b = Path(bundle) if bundle is not None else bundle_path()
    if not b.is_file():
        raise FileNotFoundError(
            f"demo data not found at {b}. Run `wattnot demo` from the repo "
            "checkout so that data/dash/ is present (re-clone if it is missing)."
        )
    chosen = find_free_port(port, host)
    # port 0 means "any free port", so a different result is expected, not "busy".
    return DemoPlan(
        host=host,
        port=chosen,
        url=f"http://{host}:{chosen}/",
        bundle=b,
        open_browser=open_browser,
        port_changed=port != 0 and chosen != port,
        requested_port=port,
    )


def banner(plan: DemoPlan) -> str:
    """The friendly text printed before the server starts."""
    lines = [
        "",
        "  WattNot demo — a recorded H100 run, replayed on your machine.",
    ]
    if plan.port_changed:
        lines.append(
            f"  (port {plan.requested_port} was busy, using {plan.port} instead)"
        )
    lines += [
        f"  Open:  {plan.url}",
        "",
        "  What you'll see: the measured power-cap sweep (RUN2), what the",
        "  planner decided at each step, the quality and p99 gate, and a signed",
        "  receipt you can re-verify. Start on the Proof tab.",
        "",
        "  Nothing runs live: no GPU, no network, no Meta/Baseten/Modal key,",
        "  no money spent. Every number is labelled MEASURED, ESTIMATE or mock.",
        "",
        "  Press Ctrl+C to stop.",
        "",
    ]
    return "\n".join(lines)


def _open_browser_soon(url: str, delay: float = 1.0) -> None:
    """Open the browser shortly after the server has had time to bind."""

    def _open() -> None:
        try:
            webbrowser.open(url)
        except Exception:  # pragma: no cover - a headless box simply has none
            pass

    timer = threading.Timer(delay, _open)
    timer.daemon = True
    timer.start()


def _default_serve(plan: DemoPlan) -> int:
    from wattnot.dash.server import serve

    # replay_only keeps the demo fully offline: no live capture is followed and
    # no Meta call is ever attempted, whatever is in the environment.
    return serve(plan.host, plan.port, replay_only=True)


def add_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--host", default=DEFAULT_HOST)
    p.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help="preferred port; a free one is chosen if it is busy (default %(default)s)",
    )
    p.add_argument(
        "--no-browser", action="store_true", help="do not open a web browser"
    )


def run(args: argparse.Namespace, *, serve=_default_serve) -> int:
    if not python_ok():
        print(
            f"wattnot demo: needs Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]} or newer; "
            f"this is {sys.version.split()[0]}. See the README troubleshooting section.",
            file=sys.stderr,
        )
        return 1
    try:
        plan = prepare(args.host, args.port, open_browser=not args.no_browser)
    except FileNotFoundError as e:
        print(f"wattnot demo: {e}", file=sys.stderr)
        return 2
    print(banner(plan), flush=True)
    if plan.open_browser:
        _open_browser_soon(plan.url)
    return serve(plan)
