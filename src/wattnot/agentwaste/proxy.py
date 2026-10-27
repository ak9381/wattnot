from __future__ import annotations

from collections.abc import Callable

from wattnot.agentwaste.config import WasteConfig
from wattnot.agentwaste.guard import SharedBudget, WasteGuard


class WasteProxy:
    """Wrap an OpenAI-compatible client with waste checks for one agent run."""

    def __init__(
        self,
        upstream: Callable[..., dict],
        config: WasteConfig | None = None,
        shared: SharedBudget | None = None,
    ) -> None:
        self._upstream = upstream
        self._config = config or WasteConfig()
        self._shared = shared
        self._guard = WasteGuard(self._config, shared)

    def reset(self) -> None:
        """Start a new run with fresh counters."""
        self._guard = WasteGuard(self._config, self._shared)

    def __call__(self, body: dict, **kw) -> dict:
        return self.proxy(body, **kw)

    def proxy(self, body: dict, **kw) -> dict:
        """Same call shape as GuardedClient: client(body, purpose=...)."""
        verdict = self._guard.before(body)
        if verdict.action == "stop":
            assert verdict.reason is not None
            model = body.get("model", "")
            return self._guard.stop_response(verdict.reason, model=str(model))
        response = self._upstream(verdict.body, **kw)
        return self._guard.after(response)

    def stats(self) -> dict:
        """Current counters for this run."""
        return self._guard.stats()

    @property
    def stopped(self) -> bool:
        """True once the run was stopped early or finished via done."""
        return bool(self._guard.stats()["stopped"])

    @property
    def stop_reason(self) -> str | None:
        """Why the run stopped, or None while it is still going."""
        return self._guard.stats()["stop_reason"]
