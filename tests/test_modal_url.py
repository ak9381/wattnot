"""resolve_modal_url: env -> opt-in SDK discovery -> cache -> clear error, with NO network.

Discovery is exercised only through an injected callable or a monkeypatched ``_sdk_discover``;
the real Modal SDK is never called, so the whole suite runs offline.
"""

from __future__ import annotations

import pytest

from wattnot.cloud import modal_url as mu
from wattnot.cloud.modal_url import (
    ModalUrlUnset,
    resolve_modal_base_url,
    resolve_modal_url,
)


def test_primary_env_wins_over_legacy():
    env = {"WATTNOT_MODAL_URL": "https://primary.modal.run",
           "MODAL_ENDPOINT_URL": "https://legacy.modal.run"}
    # discover must not even be consulted when env resolves
    boom = lambda: (_ for _ in ()).throw(AssertionError("discovery ran with env set"))
    assert resolve_modal_url(env, discover=boom) == "https://primary.modal.run"


def test_legacy_env_still_honoured():
    env = {"MODAL_ENDPOINT_URL": "https://legacy.modal.run"}
    assert resolve_modal_url(env) == "https://legacy.modal.run"


def test_discovery_used_when_env_empty():
    calls = []
    disc = lambda: (calls.append(1), "https://discovered.modal.run")[1]
    assert resolve_modal_url({}, discover=disc) == "https://discovered.modal.run"
    assert calls == [1]


def test_discovery_is_opt_in_and_offline_by_default(monkeypatch):
    # with no discover injected and the opt-in flag off, _sdk_discover must never be called,
    # so nothing dials the network; resolution falls through to the (missing) cache -> error.
    monkeypatch.setattr(mu, "_sdk_discover",
                        lambda: (_ for _ in ()).throw(AssertionError("SDK discovery dialed out")))
    with pytest.raises(ModalUrlUnset):
        resolve_modal_url({}, cache_path="/nonexistent/wattnot/modal_url")


def test_opt_in_flag_triggers_sdk_discovery(monkeypatch):
    monkeypatch.setattr(mu, "_sdk_discover", lambda: "https://sdk.modal.run")
    env = {"WATTNOT_MODAL_URL_DISCOVER": "1"}
    assert resolve_modal_url(env, cache_path="/nonexistent") == "https://sdk.modal.run"


def test_discovery_failure_falls_through_to_cache(tmp_path):
    cache = tmp_path / "modal_url"
    cache.write_text("https://cached.modal.run\n", encoding="utf-8")
    assert resolve_modal_url({}, discover=lambda: None, cache_path=cache) == \
        "https://cached.modal.run"


def test_cache_env_points_at_file(tmp_path):
    cache = tmp_path / "url.txt"
    cache.write_text("https://from-cache-env.modal.run", encoding="utf-8")
    env = {"WATTNOT_MODAL_URL_CACHE": str(cache)}
    assert resolve_modal_url(env) == "https://from-cache-env.modal.run"


def test_clear_error_names_both_env_vars():
    with pytest.raises(ModalUrlUnset) as ei:
        resolve_modal_url({}, cache_path="/nonexistent")
    msg = str(ei.value)
    assert "WATTNOT_MODAL_URL" in msg and "MODAL_ENDPOINT_URL" in msg


def test_base_url_normalises_v1_exactly_once():
    assert resolve_modal_base_url({"WATTNOT_MODAL_URL": "https://x.modal.run"}) == \
        "https://x.modal.run/v1"
    assert resolve_modal_base_url({"WATTNOT_MODAL_URL": "https://x.modal.run/v1/"}) == \
        "https://x.modal.run/v1"


def test_sdk_discover_never_raises(monkeypatch):
    # even if the SDK import or lookup blows up, discovery returns None (no leak, no network error)
    import builtins
    real_import = builtins.__import__

    def _no_modal(name, *a, **k):
        if name == "modal":
            raise ImportError("modal not installed")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", _no_modal)
    assert mu._sdk_discover() is None
