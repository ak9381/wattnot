"""`python -m wattnot.longrun.watchdog ...` — the shared check command the agent layers run."""

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
