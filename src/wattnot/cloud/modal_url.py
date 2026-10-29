"""Resolve the Modal web-endpoint base URL, with no network on the test or default path.

The endpoint URL used to be read straight from a single env var. This resolver makes it
configurable and adds safe fallbacks. Resolution order:

  1. ``WATTNOT_MODAL_URL`` env var -- the configured endpoint (preferred).
  2. ``MODAL_ENDPOINT_URL`` env var -- the legacy name, still honoured.
  3. Modal SDK auto-discovery of the deployed function's ``web_url`` -- OPT-IN and guarded.
     It touches the network, so it only runs when a ``discover`` callable is injected (tests)
     or ``WATTNOT_MODAL_URL_DISCOVER=1`` is set; any failure (SDK absent, app not deployed,
     network down) falls through. So the default path and the whole test suite never dial out.
  4. a cached file (``WATTNOT_MODAL_URL_CACHE``, else ``~/.cache/wattnot/modal_url``).
  5. otherwise a clear error naming every option.

Nothing here reads or prints a credential: the URL is not a secret, and the proxy token stays
in the transport layer.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path

PRIMARY_URL_ENV = "WATTNOT_MODAL_URL"
LEGACY_URL_ENV = "MODAL_ENDPOINT_URL"
DISCOVER_ENV = "WATTNOT_MODAL_URL_DISCOVER"
CACHE_ENV = "WATTNOT_MODAL_URL_CACHE"
MODAL_APP_NAME = "wattnot-spark-topk-draft"
MODAL_FUNCTION_NAME = "b200_serve"
DEFAULT_CACHE = Path.home() / ".cache" / "wattnot" / "modal_url"


class ModalUrlUnset(RuntimeError):
    """No Modal endpoint URL could be resolved from env, discovery or cache."""


def _clean(value: str | None) -> str | None:
    value = (value or "").strip()
    return value or None


def _sdk_discover() -> str | None:
    """Best-effort SDK lookup of the deployed function's web URL. Touches the network, so it is
    only called behind the opt-in guard below. Returns None on any failure; never raises."""
    try:
        import modal  # optional dependency; present only on a Modal CLI host
        fn = modal.Function.from_name(MODAL_APP_NAME, MODAL_FUNCTION_NAME)
        return _clean(fn.get_web_url())
    except Exception:
        return None


def _from_cache(path) -> str | None:
    try:
        return _clean(Path(path).read_text(encoding="utf-8"))
    except OSError:
        return None


def resolve_modal_url(env: Mapping[str, str] | None = None, *,
                      discover: Callable[[], str | None] | None = None,
                      cache_path=None) -> str:
    """Return the Modal endpoint URL per the order documented at module top, else raise
    ``ModalUrlUnset``. ``discover`` and ``cache_path`` are injectable so tests never dial out
    and never read the real cache."""
    # read_env is the package's single sanctioned reader of the process environment
    # (when env is None it reads the live environment); keep all env access flowing through it.
    from wattnot.cloud.transport import read_env
    url = _clean(read_env(PRIMARY_URL_ENV, env)) or _clean(read_env(LEGACY_URL_ENV, env))
    if url is None:
        disc = discover
        if disc is None and _clean(read_env(DISCOVER_ENV, env)) == "1":
            disc = _sdk_discover
        if disc is not None:
            url = _clean(disc())
    if url is None:
        cache = (cache_path if cache_path is not None
                 else _clean(read_env(CACHE_ENV, env)) or DEFAULT_CACHE)
        url = _from_cache(cache)
    if url is None:
        raise ModalUrlUnset(
            f"No Modal endpoint URL: set {PRIMARY_URL_ENV} (or the legacy {LEGACY_URL_ENV}), "
            f"enable SDK discovery with {DISCOVER_ENV}=1 on a host where the app is deployed, "
            f"or write the URL to a cache file ({CACHE_ENV}, default {DEFAULT_CACHE}).")
    return url


def resolve_modal_base_url(env: Mapping[str, str] | None = None, **kw) -> str:
    """``resolve_modal_url`` normalised to end in exactly one ``/v1`` (the OpenAI base path)."""
    url = resolve_modal_url(env, **kw).rstrip("/")
    return url if url.endswith("/v1") else url + "/v1"


__all__ = ["resolve_modal_url", "resolve_modal_base_url", "ModalUrlUnset",
           "PRIMARY_URL_ENV", "LEGACY_URL_ENV", "DISCOVER_ENV", "CACHE_ENV",
           "MODAL_APP_NAME", "MODAL_FUNCTION_NAME", "DEFAULT_CACHE"]
