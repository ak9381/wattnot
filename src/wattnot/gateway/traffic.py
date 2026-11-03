"""Simulated traffic: real requests sent from the backend through the running gateway.

Workloads: mixed, text, agent, images, voice. Optional side-by-side "without WattNot" arm: the
same request goes straight to the upstream at the baseline tier, logged to its own file.
Everything here is labelled "Simulated traffic" (the requests are real, the users are not).
A run stops itself once its own spend passes ``max_usd`` (default $1).
"""
from __future__ import annotations

import base64
import io
import json
import threading
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
VOICE_DIR = ROOT / "data" / "voice" / "spoken"
WORKLOADS = ("mixed", "text", "agent", "langchain", "images", "voice")
SPEEDS = {"slow": 2.0, "normal": 0.5, "fast": 0.0}
# per-request token guesses from the 60-request live demo (MEASURED there; ESTIMATE here)
TOKENS = {"langchain": (700, 300), "text": (60, 250), "agent": (330, 120), "images": (900, 60), "voice": (80, 200), "long": (1600, 40)}
PRICE_IN, PRICE_OUT = 1.25, 4.25    # muse-spark-1.3 $ per 1M (meta/ledger.py)

EASY = ["What is 17 + 26?", "Capital of Kenya?", "What is 12 * 11?", "Is 91 prime? Answer yes or no.",
        "How many minutes in 3.5 hours?", "What is 15% of 240?", "Name the largest planet.",
        "Convert 5 km to metres.", "What is the square root of 196?", "What is 2 to the power 10?"]
HARD = ["A train leaves at 9:40 and arrives at 13:05. It stopped 3 times for 7 minutes each. How many minutes was it moving? Number only.",
        "Smallest positive n with n mod 3 = 2, n mod 5 = 3, n mod 7 = 2? Number only.",
        "A price goes up 20% then down 20% and ends at 96. Start price? Number only.",
        "Write a Python function that returns the n-th Fibonacci number iteratively. Code only."]
LONG = "Meeting notes, platform team. " + " ".join(
    f"Item {i}: the team discussed service {i} latency, owners, next steps and the rollout window for region {i % 5}."
    for i in range(60))


def _image_uri() -> str:
    from PIL import Image, ImageDraw, ImageFont
    im = Image.new("RGB", (1600, 1000), "white")
    d = ImageDraw.Draw(im)
    try:
        font = ImageFont.load_default(size=120)
    except TypeError:
        font = None
    d.text((150, 400), "TOTAL 4250", fill="black", font=font)
    b = io.BytesIO()
    im.save(b, format="PNG")
    return "data:image/png;base64," + base64.b64encode(b.getvalue()).decode()


def _agent_body() -> dict:
    call = {"type": "function", "function": {"name": "search", "arguments": json.dumps({"q": "H100 power limit"})}}
    msgs = [{"role": "user", "content": "Find the H100 default power limit, then answer in one line."}]
    for i in range(4):
        msgs += [{"role": "assistant", "content": None, "tool_calls": [dict(call, id=f"c{i}")]},
                 {"role": "tool", "tool_call_id": f"c{i}", "content": "H100 SXM default power limit: 700 W."}]
    return {"messages": msgs, "tools": [{"type": "function", "function": {
        "name": "search", "parameters": {"type": "object", "properties": {"q": {"type": "string"}}}}}]}


LC_TASKS = ["A rack has 8 GPUs at 350 W each. Use the multiply tool to get the rack's watts, then answer in one line.",
            "Use the multiply tool: 24 hours times 2.8 kW. Give the kWh in one line.",
            "Use the add tool to add 375 and 325, then say the total watts in one line."]


def langchain_agent(base_url: str, task: str, run_id: str, max_steps: int = 4) -> dict:
    """A real small LangChain agent: ChatOpenAI pointed at the gateway, two tools, a tool loop."""
    from langchain_core.messages import HumanMessage, ToolMessage
    from langchain_core.tools import tool
    from langchain_openai import ChatOpenAI

    @tool
    def multiply(a: float, b: float) -> float:
        """Multiply two numbers."""
        return a * b

    @tool
    def add(a: float, b: float) -> float:
        """Add two numbers."""
        return a + b

    tools = {"multiply": multiply, "add": add}
    llm = ChatOpenAI(base_url=base_url, api_key="unused", model="muse-spark-1.3", max_retries=0, timeout=120,
                     default_headers={"x-wattnot-run": run_id, "x-wattnot-traffic": "simulated"})
    agent = llm.bind_tools(list(tools.values()))
    msgs = [HumanMessage(task)]
    steps = 0
    for steps in range(1, max_steps + 1):
        ai = agent.invoke(msgs)
        msgs.append(ai)
        if not ai.tool_calls:
            break
        for tc in ai.tool_calls:
            fn = tools.get(tc["name"])
            out = fn.invoke(tc["args"]) if fn else "unknown tool"
            msgs.append(ToolMessage(str(out), tool_call_id=tc["id"]))
    return {"answer": str(msgs[-1].content)[:200], "llm_calls": steps}


def plan(workload: str, n: int) -> list[tuple[str, dict]]:
    if workload not in WORKLOADS:
        raise ValueError(f"workload must be one of {WORKLOADS}")
    wavs = sorted(VOICE_DIR.glob("*.wav"))
    img = _image_uri() if workload in ("mixed", "images") else None
    out = []
    for i in range(int(n)):
        kind = workload
        if workload == "mixed":
            kind = ["text"] * 5 + ["hard", "hard", "agent", "langchain", "long", "images", "voice"]
            kind = kind[i % 12]
        if kind == "text":
            out.append(("text", {"messages": [{"role": "user", "content": EASY[i % len(EASY)]}]}))
        elif kind == "hard":
            out.append(("text", {"messages": [{"role": "user", "content": HARD[i % len(HARD)]}]}))
        elif kind == "agent":
            out.append(("agent", _agent_body()))
        elif kind == "long":
            out.append(("long", {"messages": [{"role": "system", "content": LONG},
                                              {"role": "user", "content": "Which region is item 42 in? Number only."}]}))
        elif kind == "images":
            out.append(("images", {"messages": [{"role": "user", "content": [
                {"type": "text", "text": "What total is written in this image? Number only."},
                {"type": "image_url", "image_url": {"url": img}}]}]}))
        elif kind == "langchain":
            out.append(("langchain", {"task": LC_TASKS[i % len(LC_TASKS)]}))
        else:
            out.append(("voice", {"wav": str(wavs[i % len(wavs)]) if wavs else None}))
    return out


def estimate(workload: str, n: int, with_without: bool = False) -> dict:
    """Upper-end $ for a run, before Start. ESTIMATE from the live demo's token counts."""
    usd = 0.0
    for kind, _ in plan(workload, n) if workload != "voice" else [("voice", {})] * int(n):
        tin, tout = TOKENS.get(kind, TOKENS["text"])
        usd += (tin * PRICE_IN + tout * PRICE_OUT) / 1e6
    usd *= 1.6          # headroom: step-ups and the 10% shadow sample
    if with_without:
        usd *= 2.2      # the without arm runs the baseline (high effort) tier, longer answers
    return {"usd": round(usd, 4), "label": "ESTIMATE", "requests": int(n) * (2 if with_without else 1),
            "under_1_usd": usd < 1.0}


class TrafficRun:
    """One background run. ``send`` posts a chat body to the gateway; ``direct`` (optional) sends
    it straight to the upstream for the without arm; ``transcribe`` turns a wav into text."""

    def __init__(self, send, *, direct=None, transcribe=None, without_log: Path | None = None,
                 max_usd: float = 1.0, lc_runner=None, spent=None):
        self.lc_runner = lc_runner or langchain_agent
        self.spent = spent            # callable(run_prefix) -> $ billed through the gateway so far
        self.base_url = None
        self.send, self.direct, self.transcribe = send, direct, transcribe
        self.without_log = Path(without_log) if without_log else None
        self.max_usd = max_usd
        self.state = {"running": False, "done": 0, "total": 0, "errors": 0, "usd": 0.0,
                      "stopped": None, "label": "Simulated traffic", "without": {"done": 0, "usd": 0.0}}
        self._stop = threading.Event()
        self.thread: threading.Thread | None = None

    def start(self, workload="mixed", n=60, speed="normal", with_without=False, base_url=None) -> dict:
        self.base_url = base_url
        if self.thread and self.thread.is_alive():
            raise RuntimeError("traffic is already running")
        if int(n) not in (20, 60, 200):
            raise ValueError("requests must be 20, 60 or 200")
        if speed not in SPEEDS:
            raise ValueError(f"speed must be one of {list(SPEEDS)}")
        items = plan(workload, n)
        self._stop.clear()
        self.state.update(running=True, done=0, total=len(items), errors=0, usd=0.0, stopped=None,
                          workload=workload, with_without=bool(with_without), without={"done": 0, "usd": 0.0})
        self.thread = threading.Thread(target=self._run, args=(items, SPEEDS[speed], with_without), daemon=True)
        self.thread.start()
        return dict(self.state)

    def stop(self) -> None:
        self._stop.set()

    def _run(self, items, gap, with_without) -> None:
        try:
            for i, (kind, body) in enumerate(items):
                if self._stop.is_set():
                    self.state["stopped"] = "stopped by you"
                    break
                if self.state["usd"] + self.state["without"]["usd"] >= self.max_usd:
                    self.state["stopped"] = f"hard stop: spend reached ${self.max_usd:.2f}"
                    break
                if kind == "voice":
                    text = None
                    if body.get("wav") and self.transcribe:
                        try:
                            text = self.transcribe(Path(body["wav"]).read_bytes())
                        except Exception:  # noqa: BLE001
                            text = None
                    body = {"messages": [{"role": "user", "content": (text or "What is 6 times 7?") + " Answer in one line."}]}
                if kind == "langchain":
                    try:
                        if not self.base_url:
                            raise RuntimeError("no gateway URL")
                        out = self.lc_runner(self.base_url, body["task"], f"sim-lc-{i}")
                        self.state.setdefault("langchain", []).append(out)
                        if self.spent:
                            self.state["usd"] = self.spent()
                    except Exception as e:  # noqa: BLE001
                        self.state["errors"] += 1
                        self.state["last_error"] = type(e).__name__
                    self.state["done"] = i + 1
                    if gap:
                        time.sleep(gap)
                    continue   # no without arm: the agent's own calls are the workload
                body = {"model": "muse-spark-1.3", **body}
                try:
                    rec = self.send(body, {"x-wattnot-run": f"sim-{i}", "x-wattnot-traffic": "simulated"})
                    self.state["usd"] += float((rec or {}).get("usd") or 0)
                except Exception:  # noqa: BLE001
                    self.state["errors"] += 1
                if with_without and self.direct:
                    try:
                        w = self.direct(body)
                        self.state["without"]["done"] += 1
                        self.state["without"]["usd"] += float(w.get("usd") or 0)
                        if self.without_log:
                            self.without_log.parent.mkdir(parents=True, exist_ok=True)
                            with self.without_log.open("a") as f:
                                f.write(json.dumps({"ts": round(time.time(), 3), "kind": kind, **w,
                                                    "label": "Simulated traffic, without WattNot"}) + "\n")
                    except Exception:  # noqa: BLE001
                        self.state["errors"] += 1
                self.state["done"] = i + 1
                if gap:
                    time.sleep(gap)
        finally:
            self.state["usd"] = round(self.state["usd"], 5)
            self.state["without"]["usd"] = round(self.state["without"]["usd"], 5)
            self.state["running"] = False
