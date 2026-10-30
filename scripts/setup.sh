#!/usr/bin/env bash
# WattNot one-time setup for macOS (Intel and Apple Silicon) and Linux.
# No GPU, no network beyond PyPI, no Meta/Baseten/Modal key, no money spent.
#
#   bash scripts/setup.sh            # make a .venv and install WattNot into it
#   source .venv/bin/activate        # then, in your shell:
#   wattnot demo                     # opens the dashboard on the bundled replay
#
# Exit codes: 0 ok, 1 no suitable Python found, 2 install failed.
set -eu
cd "$(dirname "$0")/.."

MIN="3.11"

say() { printf '%s\n' "$*"; }
die() { printf 'setup.sh: %s\n' "$*" >&2; exit "${2:-2}"; }

# Pick the newest Python >= 3.11 on the PATH. macOS and some Linux images only
# ship `python3`, so we probe versioned names first and fall back to it.
pick_python() {
  for cand in python3.13 python3.12 python3.11 python3 python; do
    if command -v "$cand" >/dev/null 2>&1; then
      if "$cand" -c 'import sys; raise SystemExit(0 if sys.version_info[:2] >= (3, 11) else 1)' 2>/dev/null; then
        printf '%s' "$cand"
        return 0
      fi
    fi
  done
  return 1
}

PY="$(pick_python)" || die "could not find Python $MIN or newer.
  Install it from https://www.python.org/downloads/ (or 'brew install python@3.12'
  on macOS), open a new terminal, and run this script again." 1

say "Using $("$PY" -V 2>&1) at $(command -v "$PY")"

if [ ! -d .venv ]; then
  say "Creating a virtual environment in .venv ..."
  "$PY" -m venv .venv || die "could not create .venv (is the python3-venv package installed?)."
else
  say "Reusing the existing .venv"
fi

VPY=".venv/bin/python"
[ -x "$VPY" ] || die "the virtual environment looks broken; delete .venv and run this script again."

say "Upgrading pip ..."
"$VPY" -m pip install --upgrade pip >/dev/null || die "could not upgrade pip."

say "Installing WattNot (pip install -e .) ..."
"$VPY" -m pip install -e . || die "install failed; see the error above and the README troubleshooting section."

say ""
say "Done. Next, in this terminal:"
say "    source .venv/bin/activate"
say "    wattnot demo"
say ""
say "That opens the dashboard on the bundled replay — no GPU, no network, no keys."
