"""The hosted replay files: what the image holds, and what it never holds.

Reads the Dockerfile and .dockerignore as text. Docker itself is never run.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = (ROOT / "Dockerfile").read_text()
IGNORE = (ROOT / ".dockerignore").read_text()


def test_replay_flag_and_hosted_env_are_set():
    assert "--replay-only" in DOCKERFILE
    assert "WATTNOT_HOSTED_REPLAY=1" in DOCKERFILE


def test_non_root_user():
    assert "useradd" in DOCKERFILE
    users = [ln.split()[-1] for ln in DOCKERFILE.splitlines()
             if ln.strip().startswith("USER")]
    assert users and all(u not in ("root", "0") for u in users)


def test_only_the_serving_files_are_copied():
    copied = [ln for ln in DOCKERFILE.splitlines() if ln.strip().startswith("COPY")]
    assert copied
    for ln in copied:
        assert "tests" not in ln and "results" not in ln, ln
    for src in ("src/", "web/", "data/dash/", "docs/dashboard.md", "schemas/"):
        assert any(src in ln for ln in copied), src


def test_no_key_or_token_in_build_env():
    for ln in DOCKERFILE.splitlines():
        if ln.strip().startswith(("ENV", "ARG")):
            assert "KEY" not in ln.upper() and "TOKEN" not in ln.upper(), ln


def test_exposes_the_dashboard_port_and_serves_replay():
    assert "8765" in DOCKERFILE
    assert "wattnot.dash" in DOCKERFILE and "--replay-only" in DOCKERFILE
    assert "/app/saved/run.events.jsonl" in DOCKERFILE


def test_dockerignore_keeps_runs_keys_and_env_out():
    names = IGNORE.split()
    for name in ("*.pem", ".env", ".git", "tests/", "results/", "runs/"):
        assert name in names, name
