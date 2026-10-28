"""Drop-in base_url snippet generation and validation for major agent frameworks.

Supports:
- openai-python
- langchain
- llamaindex

Each snippet configures the framework to point at WattNot's base_url. Snippets are
validated against a local mock upstream server. Frameworks whose libraries are not
installed are skipped gracefully, with results labeled VALIDATED-LOCAL (mock upstream)
vs MOCK (not run).
"""

from __future__ import annotations

import contextlib
import http.server
import json
import threading
from collections.abc import Callable, Iterator, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

FRAMEWORKS = ("openai-python", "langchain", "llamaindex")
DEFAULT_MODEL = "meta-llama/Llama-3.1-8B-Instruct"
DEFAULT_BASE_URL = "http://127.0.0.1:8080/v1"
DEFAULT_QUESTION = "Where do the signed receipts live?"

LABEL_VALIDATED = "VALIDATED-LOCAL (mock upstream)"
LABEL_MOCK = "MOCK (not run)"

OPENAI_TEMPLATE = '''"""Plain openai-python behind WattNot. The only change: base_url."""
import os
from openai import OpenAI

BASE_URL = os.environ.get("WATTNOT_BASE_URL", "{base_url}")

def run(base_url: str = BASE_URL, question: str = "{question}") -> str:
    client = OpenAI(
        base_url=base_url,
        api_key=os.environ.get("WATTNOT_KEY", "unused"),
        default_headers={{"X-WattNot-Framework": "openai-python"}}
    )
    out = client.chat.completions.create(
        model="{model}",
        messages=[{{"role": "user", "content": question}}]
    )
    return out.choices[0].message.content

if __name__ == "__main__":
    print(run())
'''

LANGCHAIN_TEMPLATE = '''"""A LangChain chat model behind WattNot. The only change: base_url."""
import os
from langchain_openai import ChatOpenAI

BASE_URL = os.environ.get("WATTNOT_BASE_URL", "{base_url}")

def run(base_url: str = BASE_URL, question: str = "{question}") -> str:
    llm = ChatOpenAI(
        base_url=base_url,
        api_key=os.environ.get("WATTNOT_KEY", "unused"),
        model="{model}",
        default_headers={{"X-WattNot-Framework": "langchain"}}
    )
    return llm.invoke([("system", "You are a repo agent."), ("user", question)]).content

if __name__ == "__main__":
    print(run())
'''

LLAMAINDEX_TEMPLATE = '''"""A LlamaIndex LLM behind WattNot. The only change: api_base."""
import os
from llama_index.core.llms import ChatMessage
from llama_index.llms.openai_like import OpenAILike

BASE_URL = os.environ.get("WATTNOT_BASE_URL", "{base_url}")

def run(base_url: str = BASE_URL, question: str = "{question}") -> str:
    llm = OpenAILike(
        api_base=base_url,
        api_key=os.environ.get("WATTNOT_KEY", "unused"),
        model="{model}",
        is_chat_model=True,
        default_headers={{"X-WattNot-Framework": "llamaindex"}}
    )
    return llm.chat([ChatMessage(role="user", content=question)]).message.content

if __name__ == "__main__":
    print(run())
'''

TEMPLATES = {
    "openai-python": OPENAI_TEMPLATE,
    "langchain": LANGCHAIN_TEMPLATE,
    "llamaindex": LLAMAINDEX_TEMPLATE,
}


class _MockUpstreamHandler(http.server.BaseHTTPRequestHandler):
    """Local mock OpenAI-compatible chat completions backend."""

    def log_message(self, format: str, *args: Any) -> None:
        pass  # Suppress stderr noise

    def do_POST(self) -> None:
        content_len = int(self.headers.get("Content-Length", 0))
        _ = self.rfile.read(content_len) if content_len > 0 else b""

        payload = {
            "id": "chatcmpl-mock-upstream",
            "object": "chat.completion",
            "created": 1700000000,
            "model": "meta-llama/Llama-3.1-8B-Instruct",
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": "WattNot local mock upstream verified response",
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20},
        }

        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@contextlib.contextmanager
def local_mock_upstream() -> Iterator[str]:
    """Start an ephemeral mock upstream server returning OpenAI chat completions."""
    server = http.server.HTTPServer(("127.0.0.1", 0), _MockUpstreamHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}/v1"
    finally:
        server.shutdown()
        server.server_close()


@dataclass
class SnippetResult:
    framework: str
    snippet: str
    status: str  # "validated" or "skipped"
    label: str   # "VALIDATED-LOCAL (mock upstream)" or "MOCK (not run)"
    response: str | None = None
    reason: str | None = None


def generate_snippet(
    framework: str,
    *,
    base_url: str = DEFAULT_BASE_URL,
    model: str = DEFAULT_MODEL,
    question: str = DEFAULT_QUESTION,
) -> str:
    """Generate the drop-in snippet for the given framework."""
    if framework not in TEMPLATES:
        raise ValueError(f"Unknown framework '{framework}'; expected one of {FRAMEWORKS}")
    tmpl = TEMPLATES[framework]
    return tmpl.format(base_url=base_url, model=model, question=question)


def validate_openai_python(base_url: str, question: str = DEFAULT_QUESTION) -> str:
    """Run snippet using installed openai package against base_url."""
    try:
        from openai import OpenAI
    except ImportError as e:
        raise ModuleNotFoundError("openai is not installed") from e

    client = OpenAI(
        base_url=base_url,
        api_key="unused",
        default_headers={"X-WattNot-Framework": "openai-python"},
    )
    res = client.chat.completions.create(
        model=DEFAULT_MODEL,
        messages=[{"role": "user", "content": question}],
    )
    return res.choices[0].message.content or ""


def validate_langchain(base_url: str, question: str = DEFAULT_QUESTION) -> str:
    """Run snippet using installed langchain package against base_url."""
    try:
        from langchain_openai import ChatOpenAI
    except ImportError as e:
        raise ModuleNotFoundError("langchain_openai is not installed") from e

    llm = ChatOpenAI(
        base_url=base_url,
        api_key="unused",
        model=DEFAULT_MODEL,
        default_headers={"X-WattNot-Framework": "langchain"},
    )
    out = llm.invoke([("system", "You are a repo agent."), ("user", question)])
    return str(out.content)


def validate_llamaindex(base_url: str, question: str = DEFAULT_QUESTION) -> str:
    """Run snippet using installed llama-index package against base_url."""
    try:
        from llama_index.core.llms import ChatMessage
        from llama_index.llms.openai_like import OpenAILike
    except ImportError as e:
        raise ModuleNotFoundError("llama_index is not installed") from e

    llm = OpenAILike(
        api_base=base_url,
        api_key="unused",
        model=DEFAULT_MODEL,
        is_chat_model=True,
        default_headers={"X-WattNot-Framework": "llamaindex"},
    )
    out = llm.chat([ChatMessage(role="user", content=question)])
    return str(out.message.content)


VALIDATORS: dict[str, Callable[[str, str], str]] = {
    "openai-python": validate_openai_python,
    "langchain": validate_langchain,
    "llamaindex": validate_llamaindex,
}


def generate_and_validate_snippets(
    *,
    base_url: str = DEFAULT_BASE_URL,
    model: str = DEFAULT_MODEL,
    question: str = DEFAULT_QUESTION,
    out_dir: str | Path | None = None,
    custom_validators: Mapping[str, Callable[[str, str], str]] | None = None,
) -> list[SnippetResult]:
    """Generate snippets for each framework, validate against mock upstream, and write JSON."""
    validators = dict(custom_validators or VALIDATORS)
    results: list[SnippetResult] = []

    with local_mock_upstream() as upstream_url:
        target_url = base_url if base_url != DEFAULT_BASE_URL else upstream_url

        for fw in FRAMEWORKS:
            snippet = generate_snippet(fw, base_url=base_url, model=model, question=question)
            validator = validators.get(fw)

            if validator is None:
                results.append(
                    SnippetResult(
                        framework=fw,
                        snippet=snippet,
                        status="skipped",
                        label=LABEL_MOCK,
                        reason="No validator available",
                    )
                )
                continue

            try:
                ans = validator(upstream_url, question)
                results.append(
                    SnippetResult(
                        framework=fw,
                        snippet=snippet,
                        status="validated",
                        label=LABEL_VALIDATED,
                        response=ans,
                    )
                )
            except (ImportError, ModuleNotFoundError) as err:
                results.append(
                    SnippetResult(
                        framework=fw,
                        snippet=snippet,
                        status="skipped",
                        label=LABEL_MOCK,
                        reason=f"Library not installed: {err}",
                    )
                )
            except Exception as err:
                results.append(
                    SnippetResult(
                        framework=fw,
                        snippet=snippet,
                        status="error",
                        label=LABEL_MOCK,
                        reason=f"Validation error: {err}",
                    )
                )

    if out_dir is not None:
        p = Path(out_dir)
        p.mkdir(parents=True, exist_ok=True)
        json_file = p / "snippets_validation.json"
        doc = {
            "validation_mode": "local-mock-upstream",
            "target_model": model,
            "results": [asdict(r) for r in results],
        }
        json_file.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")

    return results


__all__ = [
    "DEFAULT_BASE_URL",
    "DEFAULT_MODEL",
    "FRAMEWORKS",
    "LABEL_MOCK",
    "LABEL_VALIDATED",
    "SnippetResult",
    "generate_and_validate_snippets",
    "generate_snippet",
    "local_mock_upstream",
]
