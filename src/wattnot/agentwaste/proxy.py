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
        context_saver: object | None = None,
    ) -> None:
        self._upstream = upstream
        self._config = config or WasteConfig()
        self._shared = shared
        self._guard = WasteGuard(self._config, shared)
        self._saver = context_saver
        self._saver_before = 0
        self._saver_after = 0

    def reset(self) -> None:
        """Start a new run with fresh counters."""
        self._guard = WasteGuard(self._config, self._shared)
        self._saver_before = 0
        self._saver_after = 0

    def __call__(self, body: dict, **kw) -> dict:
        return self.proxy(body, **kw)

    def proxy(self, body: dict, **kw) -> dict:
        """Same call shape as GuardedClient: client(body, purpose=...)."""
        verdict = self._guard.before(body)
        if verdict.action == "stop":
            assert verdict.reason is not None
            model = body.get("model", "")
            return self._guard.stop_response(verdict.reason, model=str(model))
        out_body = verdict.body
        if self._saver is not None and isinstance(out_body, dict) and out_body.get("messages"):
            saved_messages, acct = self._saver.transform(list(out_body["messages"]))
            out_body = {**out_body, "messages": saved_messages}
            self._saver_before += acct["tokens_before"]
            self._saver_after += acct["tokens_after"]
        response = self._upstream(out_body, **kw)
        return self._guard.after(response)

    def stats(self) -> dict:
        """Current counters for this run."""
        s = self._guard.stats()
        if self._saver is not None:
            s["context_saver"] = {
                "tokens_before": self._saver_before, "tokens_after": self._saver_after,
                "tokens_saved": self._saver_before - self._saver_after,
                "saved_pct": round(100 * (self._saver_before - self._saver_after)
                                   / self._saver_before, 2) if self._saver_before else 0.0,
                "label": "ESTIMATE"}
        return s

    @property
    def stopped(self) -> bool:
        """True once the run was stopped early or finished via done."""
        return bool(self._guard.stats()["stopped"])

    @property
    def stop_reason(self) -> str | None:
        """Why the run stopped, or None while it is still going."""
        return self._guard.stats()["stop_reason"]
