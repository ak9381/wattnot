"""Shared test setup."""

import pytest


@pytest.fixture(autouse=True)
def _proposals_in_tmp(tmp_path_factory, monkeypatch):
    """No test may write proposals or an audit trail into the working directory."""
    monkeypatch.setenv("WATTNOT_PROPOSALS_DIR", str(tmp_path_factory.mktemp("proposals")))


# Slow tests (~20 s each). Quick feature check: python -m pytest -q -m "not slow" <files>.
# The full suite (including these) runs at integration and final review. See docs/TESTING.md.
import pytest as _pytest_slow

_SLOW_FILES = {"test_resume.py", "test_proof_events.py"}


def pytest_configure(config):
    config.addinivalue_line("markers", "slow: long-running rehearsal/resume tests")


def pytest_collection_modifyitems(config, items):
    for item in items:
        if item.fspath.basename in _SLOW_FILES:
            item.add_marker(_pytest_slow.mark.slow)
