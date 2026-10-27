"""Shared test setup."""

import pytest


@pytest.fixture(autouse=True)
def _proposals_in_tmp(tmp_path_factory, monkeypatch):
    """No test may write proposals or an audit trail into the working directory."""
    monkeypatch.setenv("WATTNOT_PROPOSALS_DIR", str(tmp_path_factory.mktemp("proposals")))
