"""Context-saver middlewares and the chain that runs them with token accounting and a gate.

Each middleware takes the chat ``messages`` and returns new messages plus a small stats dict. The
:class:`ContextSaver` runs them in order, counts tokens before and after (an ESTIMATE from text
length, since the server's tokenizer is not available off-GPU), and offers a quality gate that
rolls a cut back when the pass rate drops by more than one point.

No network, no secrets. Summarisation goes through an injected callable so tests use a mock and a
live run uses a Llama 8B client.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

# ~4 characters per token is the usual rough rule for English text; good enough for an ESTIMATE.
CHARS_PER_TOKEN = 4
_WORD = re.compile(r"[a-z0-9]{4,}")
_STOP = {"this", "that", "with", "from", "have", "what", "when", "which", "there", "their",
         "about", "would", "could", "should", "these", "those", "into", "then", "than"}


def _text(content: object) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    try:
        return json.dumps(content, sort_keys=True, separators=(",", ":"), default=str)
    except (TypeError, ValueError):
        return str(content)


def estimate_tokens(text: str) -> int:
    """Rough token count for a string (ESTIMATE): about one token per four characters."""
    return max(0, round(len(text) / CHARS_PER_TOKEN))


def _message_tokens(msg: dict) -> int:
    n = estimate_tokens(_text(msg.get("content")))
    for call in msg.get("tool_calls") or []:
        fn = (call or {}).get("function") or {}
        n += estimate_tokens(_text(fn.get("name", "")) + _text(fn.get("arguments", "")))
    return n


def conversation_tokens(messages: Sequence[dict]) -> int:
    """ESTIMATE of the tokens a chat would cost (content + tool-call names/arguments)."""
    return sum(_message_tokens(m) for m in messages if isinstance(m, dict))


def _keywords(text: str) -> set[str]:
    return {w for w in _WORD.findall(text.lower()) if w not in _STOP}


def _canon_args(arguments: object) -> str:
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except ValueError:
            return arguments.strip()
    try:
        return json.dumps(arguments, sort_keys=True, separators=(",", ":"), default=str)
    except (TypeError, ValueError):
        return str(arguments)


class MockSummarizer:
    """Deterministic, offline stand-in for a Llama 8B summariser (no network).

    Keeps the first sentence and the salient words, so a summary is shorter than the source but
    still carries its keywords for retrieval. Counts how many times it was called.
    """

    model = "meta-llama/Llama-3.1-8B-Instruct (mock)"

    def __init__(self, max_chars: int = 160) -> None:
        self.max_chars = max_chars
        self.calls = 0

    def __call__(self, text: str) -> str:
        self.calls += 1
        head = re.split(r"(?<=[.!?])\s", text.strip(), maxsplit=1)[0]
        if len(head) > self.max_chars:
            head = head[: self.max_chars - 1].rstrip() + "…"
        return head


@dataclass
class _Chunk:
    ref: str
    text: str
    keywords: set[str]
    role: str


class ChunkStore:
    """Summaries of compressed turns, retrievable by keyword overlap with a query."""

    def __init__(self) -> None:
        self._chunks: list[_Chunk] = []

    def add(self, text: str, role: str) -> str:
        ref = f"c{len(self._chunks) + 1}"
        self._chunks.append(_Chunk(ref, text, _keywords(text), role))
        return ref

    def __len__(self) -> int:
        return len(self._chunks)

    def retrieve(self, query: str, k: int = 3) -> list[_Chunk]:
        qk = _keywords(query)
        scored = [(len(qk & c.keywords), -i, c) for i, c in enumerate(self._chunks)]
        scored.sort(reverse=True)
        picked = [c for score, _, c in scored if score > 0][:k]
        return picked or [c for _, _, c in scored[:k]]  # fall back to most recent if nothing matches


@dataclass
class MemoryCompressionMiddleware:
    """Summarise old agent turns with Llama 8B, keep a chunk store, retrieve relevant chunks back.

    The system prompt and the last ``keep_last`` messages stay verbatim. Older non-system messages
    are summarised (one chunk each) into ``store``; then the chunks most relevant to the latest
    user message are stitched back as one compact recap, placed right after the system prompt.
    """

    summarize: Callable[[str], str] = field(default_factory=MockSummarizer)
    keep_last: int = 4
    min_old: int = 2
    retrieve_k: int = 3
    store: ChunkStore = field(default_factory=ChunkStore)
    name: str = "memory_compression"

    def apply(self, messages: list[dict]) -> tuple[list[dict], dict]:
        before = conversation_tokens(messages)
        head = [m for m in messages if isinstance(m, dict) and m.get("role") == "system"]
        rest = [m for m in messages if isinstance(m, dict) and m.get("role") != "system"]
        model = getattr(self.summarize, "model", "summarizer")
        if len(rest) - self.keep_last < self.min_old:
            return messages, {"name": self.name, "applied": False, "tokens_before": before,
                              "tokens_after": before, "chunks_made": 0, "chunks_retrieved": 0,
                              "summarize_calls": 0, "model": model}
        # Split old (summarised away) from recent (kept verbatim). Never cut a tool-call/tool-reply
        # pair: a kept `tool` reply whose assistant `tool_calls` message was summarised away is an
        # orphan the server rejects (HTTP 400). If the boundary lands on a tool reply, move it back
        # so the assistant that made the call stays with its reply -- the whole pair in `recent`.
        split = len(rest) - self.keep_last
        while split > 0 and str(rest[split].get("role")) == "tool":
            split -= 1
        if split <= 0:
            return messages, {"name": self.name, "applied": False, "tokens_before": before,
                              "tokens_after": before, "chunks_made": 0, "chunks_retrieved": 0,
                              "summarize_calls": 0, "model": model}
        old, recent = rest[:split], rest[split:]
        calls0 = getattr(self.summarize, "calls", 0)
        for m in old:
            text = _text(m.get("content")) or _text(m.get("tool_calls") or [{}])
            if text.strip():
                self.store.add(self.summarize(text), str(m.get("role", "")))
        query = next((_text(m.get("content")) for m in reversed(recent)
                      if m.get("role") == "user"), _text(recent[-1].get("content")) if recent else "")
        picked = self.store.retrieve(query, self.retrieve_k)
        recap_body = "Earlier context (compressed, retrieved by relevance):\n" + \
            "\n".join(f"- [{c.ref}] {c.text}" for c in picked)
        recap = {"role": "system", "content": recap_body}
        out = [*head, recap, *recent]
        after = conversation_tokens(out)
        calls = getattr(self.summarize, "calls", 0) - calls0 if hasattr(self.summarize, "calls") else len(old)
        return out, {"name": self.name, "applied": True, "tokens_before": before,
                     "tokens_after": after, "chunks_made": len(old),
                     "chunks_retrieved": len(picked), "summarize_calls": calls, "model": model}


@dataclass
class ToolDedupMiddleware:
    """Replace a repeated file read or an identical tool result with a short reference.

    The first time a tool result (or a tool call with the same name and arguments) appears it is
    kept in full and given a reference id. Later identical results are swapped for a one-line
    pointer to that id, with the number of characters omitted, so the model can still see it ran.
    """

    name: str = "tool_dedup"

    def apply(self, messages: list[dict]) -> tuple[list[dict], dict]:
        before = conversation_tokens(messages)
        # tool_call_id -> (name, canon_args) from assistant tool_calls, to spot repeated reads
        call_sig: dict[str, str] = {}
        for m in messages:
            if isinstance(m, dict) and m.get("role") == "assistant":
                for call in m.get("tool_calls") or []:
                    fn = (call or {}).get("function") or {}
                    cid = call.get("id")
                    if cid is not None:
                        call_sig[str(cid)] = f"{fn.get('name', '')}({_canon_args(fn.get('arguments', ''))})"
        seen_content: dict[str, str] = {}
        seen_sig: dict[str, str] = {}
        out: list[dict] = []
        deduped = 0
        chars_saved = 0
        ref_n = 0
        for m in messages:
            if not (isinstance(m, dict) and m.get("role") == "tool"):
                out.append(m)
                continue
            content = _text(m.get("content"))
            chash = hashlib.sha256(content.encode()).hexdigest()[:12]
            sig = call_sig.get(str(m.get("tool_call_id")))
            prior = seen_content.get(chash) or (seen_sig.get(sig) if sig else None)
            if prior is not None and content.strip():
                ref = {**m, "content": f"[identical to earlier result {prior}; "
                                       f"{len(content)} chars omitted]"}
                chars_saved += max(0, len(content) - len(ref["content"]))
                deduped += 1
                out.append(ref)
            else:
                ref_n += 1
                rid = f"r{ref_n}"
                seen_content.setdefault(chash, rid)
                if sig:
                    seen_sig.setdefault(sig, rid)
                out.append(m)
        after = conversation_tokens(out)
        return out, {"name": self.name, "applied": deduped > 0, "tokens_before": before,
                     "tokens_after": after, "deduped": deduped, "chars_saved": chars_saved}


class ContextSaver:
    """Run the context-saver middlewares in order and account for the tokens cut (ESTIMATE)."""

    def __init__(self, middlewares: Sequence[object] | None = None) -> None:
        if middlewares is None:
            middlewares = [ToolDedupMiddleware(), MemoryCompressionMiddleware()]
        self.middlewares = list(middlewares)

    def transform(self, messages: list[dict]) -> tuple[list[dict], dict]:
        before = conversation_tokens(messages)
        cur = messages
        stages = []
        for mw in self.middlewares:
            nxt, st = mw.apply(cur)
            # never let a stage make the prompt bigger (a reference can outweigh a tiny output)
            if conversation_tokens(nxt) > conversation_tokens(cur):
                st = {**st, "applied": False, "tokens_after": st.get("tokens_before"),
                      "skipped": "would grow the prompt"}
            else:
                cur = nxt
            stages.append(st)
        after = conversation_tokens(cur)
        saved = before - after
        return cur, {"tokens_before": before, "tokens_after": after, "tokens_saved": saved,
                     "saved_pct": round(100 * saved / before, 2) if before else 0.0,
                     "label": "ESTIMATE", "stages": stages,
                     "how": "tokens estimated from text length (~4 chars/token); ESTIMATE off-GPU"}


def quality_gate(before_pass_pct: float, after_pass_pct: float,
                 max_drop_pts: float = 1.0) -> dict:
    """Keep the cut only if the pass rate did not drop by more than ``max_drop_pts`` points."""
    drop = round(before_pass_pct - after_pass_pct, 4)
    kept = drop <= max_drop_pts
    return {"kept": kept, "drop_pts": drop, "max_drop_pts": max_drop_pts,
            "reason": ("pass rate held" if kept
                       else f"pass rate dropped {drop:g} pts (> {max_drop_pts:g}); rolled back")}


def saver_receipt_block(off: dict, on: dict, *, lever: str = "context_saver",
                        max_drop_pts: float = 1.0, parts: dict | None = None) -> dict:
    """Before/after receipt block: tokens, joules, pass rate and cache hit rate, with labels.

    ``off`` and ``on`` each carry tokens, joules, pass_pct, cache_hit_rate and their labels. The
    block never upgrades a label and records the gate verdict (rolled back on a >1-point drop).
    """

    def num(d, key):
        return {"value": d.get(key), "label": d.get(f"{key}_label", d.get("label", "ESTIMATE"))}

    gate = quality_gate(off.get("pass_pct") or 0.0, on.get("pass_pct") or 0.0, max_drop_pts)
    tok_off, tok_on = off.get("tokens") or 0, on.get("tokens") or 0
    return {
        "spec": "wattnot.context-saver/1",
        "lever": lever,
        "before": {"tokens": num(off, "tokens"), "joules": num(off, "joules"),
                   "pass_pct": num(off, "pass_pct"), "cache_hit_rate": num(off, "cache_hit_rate")},
        "after": {"tokens": num(on, "tokens"), "joules": num(on, "joules"),
                  "pass_pct": num(on, "pass_pct"), "cache_hit_rate": num(on, "cache_hit_rate")},
        "tokens_saved": tok_off - tok_on,
        "tokens_saved_pct": round(100 * (tok_off - tok_on) / tok_off, 2) if tok_off else None,
        "gate": gate,
        "kept": gate["kept"],
        "parts": parts or {},
    }


def _part(tokens_before, tokens_after, j_per_token, pass_before, pass_after, label, **extra):
    """One sub-part of the receipt's context_saver block, in the shape the dashboard reads."""
    jb = round(tokens_before * j_per_token, 3) if tokens_before is not None else None
    ja = round(tokens_after * j_per_token, 3) if tokens_after is not None else None
    gate = quality_gate(pass_before or 0.0, pass_after or 0.0)
    out = {"tokens_before": tokens_before, "tokens_after": tokens_after,
           "joules_before": jb, "joules_after": ja,
           "gate_pass_pct": pass_after, "gate_kept": gate["kept"], "label": label}
    out.update(extra)
    return out


def receipt_parts(saver_acct: dict, *, prefix_ab: dict | None = None,
                  j_per_token: float = 0.1497, pass_before: float = 0.0,
                  pass_after: float = 0.0, label: str = "ESTIMATE") -> dict:
    """Build ``context_saver.parts.{prefix,memcomp,dupcut}``.

    memcomp and dupcut come from the saver's per-stage token accounting. Joules are tokens x
    ``j_per_token`` (ESTIMATE unless the caller passes metered numbers). prefix comes from the
    prefix-cache A/B: tokens are prompt tokens that had to be prefilled (uncached share), and
    hit_rate is the on-arm cache hit rate. Missing data gives a PENDING part, never a made-up one.
    """
    stages = {s.get("name"): s for s in saver_acct.get("stages", [])}
    parts = {}
    for key, name in (("memcomp", "memory_compression"), ("dupcut", "tool_dedup")):
        st = stages.get(name)
        if st is None:
            parts[key] = {"tokens_before": None, "tokens_after": None, "joules_before": None,
                          "joules_after": None, "gate_pass_pct": None, "label": "PENDING"}
        else:
            parts[key] = _part(st.get("tokens_before"), st.get("tokens_after"), j_per_token,
                               pass_before, pass_after, label)
    if prefix_ab is None:
        parts["prefix"] = {"tokens_before": None, "tokens_after": None, "joules_before": None,
                           "joules_after": None, "gate_pass_pct": None, "hit_rate": None,
                           "label": "PENDING"}
    else:
        arms = prefix_ab["arms"]
        hr = prefix_ab["headline"]["cache_hit_rate"]
        tok = saver_acct.get("tokens_after") or 0
        uncached = round(tok * (1 - (hr.get("on") or 0.0)))
        parts["prefix"] = {
            "tokens_before": tok, "tokens_after": uncached,
            "joules_before": arms["off"]["j_per_correct"], "joules_after": arms["on"]["j_per_correct"],
            "gate_pass_pct": arms["on"]["pass_pct"]["mean"], "gate_kept": prefix_ab["kept"],
            "hit_rate": hr.get("on"), "hit_rate_before": hr.get("off"),
            "label": prefix_ab["headline"].get("label", "ESTIMATE"),
            "note": "joules are J per correct answer per arm; tokens_after = prefilled (uncached) prompt tokens",
        }
    return parts
