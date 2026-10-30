"""`wattnot demo`, the setup scripts, and cross-platform path handling.

The demo must open the bundled replay with no GPU, no network and no keys, on
Windows, macOS and Linux alike. These tests exercise the pure planning logic
(version check, free-port pick, pathlib resolution, friendly errors) without
starting a server or a browser.
"""

from __future__ import annotations

import argparse
import shutil
import socket
import subprocess
from pathlib import Path

import pytest

from wattnot import demo
from wattnot.cli import build_parser

ROOT = Path(__file__).resolve().parents[1]


def _args(**kw) -> argparse.Namespace:
    ns = build_parser().parse_args(["demo", *kw.pop("extra", [])])
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


# --- version gate ---------------------------------------------------------

@pytest.mark.parametrize(
    "ver,ok",
    [((3, 11), True), ((3, 13), True), ((4, 0), True), ((3, 10), False), ((2, 7), False)],
)
def test_python_ok(ver, ok):
    assert demo.python_ok(ver) is ok


def test_run_refuses_old_python(monkeypatch, capsys):
    monkeypatch.setattr(demo, "python_ok", lambda *a: False)
    rc = demo.run(_args(), serve=lambda plan: 0)
    assert rc == 1
    assert "Python" in capsys.readouterr().err


# --- free-port selection --------------------------------------------------

def test_find_free_port_returns_preferred_when_free():
    # Grab a free port from the OS, release it, then ask demo for it back.
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        free = s.getsockname()[1]
    assert demo.find_free_port(free) == free


def test_find_free_port_falls_back_when_busy():
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen()
        taken = busy.getsockname()[1]
        chosen = demo.find_free_port(taken)
        assert chosen != taken
        # The replacement must itself be bindable.
        with socket.socket() as check:
            check.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            check.bind(("127.0.0.1", chosen))


# --- path handling --------------------------------------------------------

def test_bundle_path_is_absolute_and_under_data_dash():
    b = demo.bundle_path()
    assert b.is_absolute()
    assert b.is_file()
    # pathlib parts, not a hand-built string, so this holds on every OS.
    assert b.parts[-3:] == ("data", "dash", "h100-replay.json")


def test_prepare_resolves_bundle_and_url():
    plan = demo.prepare("127.0.0.1", 0, open_browser=False)
    assert plan.bundle.is_file()
    assert plan.url == f"http://127.0.0.1:{plan.port}/"
    assert plan.port > 0


def test_prepare_missing_bundle_is_friendly(tmp_path):
    missing = tmp_path / "nope.json"
    with pytest.raises(FileNotFoundError) as e:
        demo.prepare(bundle=missing)
    assert "demo data not found" in str(e.value)


def test_run_reports_missing_bundle(monkeypatch, capsys):
    monkeypatch.setattr(demo, "bundle_path", lambda: Path("/does/not/exist.json"))
    rc = demo.run(_args(), serve=lambda plan: 0)
    assert rc == 2
    assert "demo data not found" in capsys.readouterr().err


# --- banner ---------------------------------------------------------------

def test_banner_is_honest_and_shows_url():
    plan = demo.prepare("127.0.0.1", 0, open_browser=False)
    text = demo.banner(plan)
    assert plan.url in text
    assert "no GPU" in text and "no network" in text
    # A freshly chosen free port should not pretend 8765 was busy.
    assert "was busy" not in text


def test_banner_notes_busy_port():
    plan = demo.DemoPlan(
        host="127.0.0.1", port=9999, url="http://127.0.0.1:9999/",
        bundle=demo.bundle_path(), open_browser=False, port_changed=True,
        requested_port=8765,
    )
    text = demo.banner(plan)
    assert "port 8765 was busy" in text and "using 9999" in text


# --- run wiring -----------------------------------------------------------

def test_run_passes_plan_to_serve_and_skips_browser(monkeypatch):
    opened: list[str] = []
    monkeypatch.setattr(demo, "_open_browser_soon", lambda url, **kw: opened.append(url))
    seen: dict = {}

    def fake_serve(plan):
        seen["plan"] = plan
        return 0

    rc = demo.run(_args(extra=["--no-browser"]), serve=fake_serve)
    assert rc == 0
    assert seen["plan"].open_browser is False
    assert opened == []  # --no-browser must not open anything


def test_run_opens_browser_by_default(monkeypatch):
    opened: list[str] = []
    monkeypatch.setattr(demo, "_open_browser_soon", lambda url, **kw: opened.append(url))
    rc = demo.run(_args(), serve=lambda plan: 0)
    assert rc == 0
    assert len(opened) == 1 and opened[0].startswith("http://")


def test_default_serve_is_replay_only(monkeypatch):
    import wattnot.dash.server as srv

    captured: dict = {}
    monkeypatch.setattr(srv, "serve", lambda host, port, **kw: captured.update(
        host=host, port=port, **kw) or 0)
    plan = demo.prepare("127.0.0.1", 0, open_browser=False)
    assert demo._default_serve(plan) == 0
    assert captured["replay_only"] is True
    assert captured["host"] == "127.0.0.1" and captured["port"] == plan.port


# --- CLI wiring -----------------------------------------------------------

def test_cli_parses_demo():
    ns = build_parser().parse_args(["demo", "--no-browser", "--port", "9100"])
    assert ns.cmd == "demo" and ns.no_browser is True and ns.port == 9100


# --- setup scripts --------------------------------------------------------

def test_setup_scripts_exist():
    assert (ROOT / "scripts" / "setup.sh").is_file()
    assert (ROOT / "scripts" / "setup.ps1").is_file()


def test_setup_sh_has_valid_syntax():
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("bash not available on this platform")
    r = subprocess.run([bash, "-n", str(ROOT / "scripts" / "setup.sh")],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_setup_scripts_point_at_demo():
    sh = (ROOT / "scripts" / "setup.sh").read_text(encoding="utf-8")
    ps = (ROOT / "scripts" / "setup.ps1").read_text(encoding="utf-8")
    assert "pip install -e ." in sh and "wattnot demo" in sh
    assert "pip install -e ." in ps and "wattnot demo" in ps
    # The install target is a venv on both, so a judge never touches system Python.
    assert ".venv" in sh and ".venv" in ps
