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
SAMPLING = {"temperature": 0, "top_p": 1, "seed": 1234, "max_tokens": 1536}
SPEEDS = {"slow": 2.0, "normal": 0.5, "fast": 0.0}
# per-request token guesses from the 60-request live demo (MEASURED there; ESTIMATE here)
TOKENS = {"langchain": (700, 300), "text": (60, 250), "agent": (330, 120), "images": (900, 60), "voice": (80, 200), "long": (1600, 40)}
PRICE_IN, PRICE_OUT = 1.25, 4.25    # muse-spark-1.3 $ per 1M (meta/ledger.py)

EASY = ["What is 17 + 26?", "Capital of Kenya?", "What is 12 * 11?", "Is 91 prime? Answer yes or no.",
        "How many minutes in 3.5 hours?", "What is 15% of 240?", "Name the largest planet.",
        "Convert 5 km to metres.", "What is the square root of 196?", "What is 2 to the power 10?"]
EASY_ANS = [43, "nairobi", 132, "no", 210, 36, "jupiter", 5000, 14, 1024]


def score(text: str, want) -> bool:
    """Harness scoring: numbers by wattnot.longrun.grade.same_number on the last number, words by match."""
    from wattnot.longrun.grade import last_number, same_number
    if isinstance(want, (int, float)):
        return same_number(last_number(text or ""), want)
    return str(want).lower() in (text or "").lower()


def _agree(a: str, b: str) -> bool:
    from wattnot.longrun.grade import last_number
    na, nb = last_number(a or ""), last_number(b or "")
    if na is not None and nb is not None:
        return abs(na - nb) <= max(0.011, abs(nb) * 1e-4)
    return (a or "").strip().lower()[:40] == (b or "").strip().lower()[:40]


def arm_agreement(w: dict, wo: dict) -> float:
    """Share of requests where the WattNot arm answered like stock. Agent turns that answer with
    tool calls (empty text) compare tool name plus canonical arguments, not empty strings (an
    empty-vs-empty text compare scored every agent turn as agreeing)."""
    tw, two = w.get("tool_calls") or [], wo.get("tool_calls") or []
    n = len(w.get("texts") or [])
    ok = 0
    for i, (a, b) in enumerate(zip(w.get("texts") or [], wo.get("texts") or [])):
        ta = [tuple(x) for x in (tw[i] if i < len(tw) else None) or []]
        tb = [tuple(x) for x in (two[i] if i < len(two) else None) or []]
        if ta or tb:
            ok += ta == tb
        else:
            ok += bool((a or "").strip() or (b or "").strip()) and _agree(a, b)
    return round(ok / max(1, n), 3)


def setup_split(per_j: list) -> dict | None:
    """One-time gate setup cost, shown on its own line instead of being charged to the WattNot arm.

    While the per-model gate warms up, each saver-on request waits for its shadow call to stock
    (run alone so its energy can be taken out). Those requests still read about 2.5x a steady
    request (take 5 agent: about 1,030 J vs 405 J, MEASURED), because the GPU sits through the
    hand-off. The request itself is still charged to the arm at the arm's steady median J per
    request; only the excess above that is the one-time setup line."""
    warm = [p for p in per_j if len(p) > 2]
    steady = sorted(p[1] for p in per_j if len(p) <= 2)
    if not warm or len(steady) < 3:
        return None
    med = steady[len(steady) // 2] if len(steady) % 2 else (steady[len(steady) // 2 - 1] + steady[len(steady) // 2]) / 2
    excess = sum(max(0.0, p[1] - med) for p in warm)
    return {"requests": len(warm), "joules": round(excess, 1), "steady_j_per_request": round(med, 1),
            "label": "one-time gate setup (shadow-check warm-up), shown apart from the WattNot arm: "
                     "total MEASURED by NVML; the split is an ESTIMATE (warm-up requests charged at the "
                     "arm's steady median J/request, the excess is setup)",
            "note": "paid once per model and saver mix; the gate keeps its decisions across restarts"}


class NvmlGpu:
    """GPU 0 through pynvml: energy counter (mJ) and power limit (mW)."""
    def __init__(self):
        import pynvml as n
        n.nvmlInit()
        self.n, self.h = n, n.nvmlDeviceGetHandleByIndex(0)

    def energy_mj(self) -> int:
        return self.n.nvmlDeviceGetTotalEnergyConsumption(self.h)

    def limit_w(self) -> int:
        return self.n.nvmlDeviceGetPowerManagementLimit(self.h) // 1000

    def default_w(self) -> int:
        return self.n.nvmlDeviceGetPowerManagementDefaultLimit(self.h) // 1000

    def set_w(self, w: int) -> None:
        self.n.nvmlDeviceSetPowerManagementLimit(self.h, int(w) * 1000)

    def can_cap(self) -> bool:
        """Can this process set the power limit? Hosted GPUs (Modal, Baseten) give no root, so
        re-setting the current limit fails there. Probed once, never changes the limit."""
        if not hasattr(self, "_can_cap"):
            try:
                self.set_w(self.limit_w())
                self._can_cap = True
            except Exception:  # noqa: BLE001 - NVML NoPermission / NotSupported
                self._can_cap = False
        return self._can_cap


CAP_UNAVAILABLE = "power cap not available on this provider"


class RemoteNvmlGpu:
    """NVML of a hosted GPU container (Modal), read over HTTP from its /wattnot/nvml route.
    Energy counter MEASURED inside the container; the power limit cannot be set (no root)."""
    label = "MEASURED (NVML energy counter in the Modal container, read over HTTP per arm)"

    def __init__(self, url: str, headers: dict | None = None, timeout: float = 15):
        self.url, self.headers, self.timeout = url, dict(headers or {}), timeout
        self._get()   # fail fast if the endpoint is down

    def _get(self) -> dict:
        import urllib.request
        req = urllib.request.Request(self.url, headers=self.headers)
        with urllib.request.urlopen(req, timeout=self.timeout) as r:  # noqa: S310
            return json.loads(r.read().decode())

    def energy_mj(self) -> int:
        return int(self._get()["energy_mj"])

    def limit_w(self):
        return None      # the cap is not ours to set; shown as "no power cap on this provider"

    def default_w(self):
        return None

    def set_w(self, w) -> None:
        raise RuntimeError(CAP_UNAVAILABLE)

    def can_cap(self) -> bool:
        return False


class EstimateMeter:
    """GPU-shaped meter for hosted APIs with no NVML (Baseten Model API): energy is the sum of
    each request's token-based joules (ESTIMATE). No power cap."""
    estimate = True
    label = "ESTIMATE (tokens x J/token per model, no GPU counter on this provider)"

    def __init__(self):
        self.mj = 0.0

    def add(self, joules) -> None:
        self.mj += float(joules or 0) * 1000

    def energy_mj(self) -> float:
        return self.mj

    def limit_w(self):
        return None

    def default_w(self):
        return None

    def set_w(self, w) -> None:
        raise RuntimeError(CAP_UNAVAILABLE)

    def can_cap(self) -> bool:
        return False


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


def load_calib(path) -> dict:
    """Per-kind measured $/request from earlier runs on this gateway: {"with": {kind: [sum, n]}, "without": ...}."""
    try:
        return json.loads(Path(path).read_text()) if path and Path(path).exists() else {}
    except (OSError, ValueError):
        return {}


def estimate(workload: str, n: int, with_without: bool = False, calib: dict | None = None,
             self_hosted: bool = False) -> dict:
    """Upper-end $ for a run, before Start (ESTIMATE). Uses the $/request measured on this gateway's
    earlier runs for each request kind when there is one (x1.15 headroom); otherwise the built-in
    token counts (x1.6). The without arm is priced on its own (measured or x1.4 of the with arm).
    self_hosted: the model runs on your own GPU, so the real API bill is $0; $ is list-price equivalent."""
    calib = calib or {}
    kinds = [k for k, _ in plan(workload, n)] if workload != "voice" else ["voice"] * int(n)
    usd_w = usd_o = 0.0
    measured = 0
    for kind in kinds:
        tin, tout = TOKENS.get(kind, TOKENS["text"])
        default = (tin * PRICE_IN + tout * PRICE_OUT) / 1e6 * 1.6
        cw = (calib.get("with") or {}).get(kind)
        co = (calib.get("without") or {}).get(kind)
        w = cw[0] / cw[1] * 1.15 if cw and cw[1] else default
        measured += bool(cw and cw[1])
        usd_w += w
        if with_without:
            usd_o += co[0] / co[1] * 1.15 if co and co[1] else w * 1.4
    usd = usd_w + usd_o
    return {"usd": round(usd, 4), "with_usd": round(usd_w, 4), "without_usd": round(usd_o, 4),
            "label": "ESTIMATE", "basis": f"measured on this gateway for {measured}/{len(kinds)} requests"
            if measured else "built-in token counts", "requests": int(n) * (2 if with_without else 1),
            "api_usd": 0.0 if self_hosted else round(usd, 4), "self_hosted": bool(self_hosted),
            "under_1_usd": usd < 1.0}


CAP_PROBE_N = 4   # paired requests per workload class before the cap is kept or rolled back
_T975 = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365, 8: 2.306, 9: 2.262}


def req_class(body: dict) -> str:
    """Workload class from the request shape: tool/agent traffic vs plain text."""
    msgs = body.get("messages") or []
    return "agent" if body.get("tools") or any(m.get("role") == "tool" or m.get("tool_calls") for m in msgs) else "text"


def item_class(kind: str, body: dict) -> str:
    """Per-request label: images and voice keep their own kind (voice used to show as text, and a
    voice run could not be told from an images run on the card); the rest by request shape."""
    return kind if kind in ("images", "voice") else req_class(body)


def cap_decision(stock_j: list[float], cap_j: list[float]) -> dict:
    """Keep the cap for a class only if MEASURED energy per request is clearly down (same rule as
    the harness: energy_clearly_down over paired requests, same prompt at stock vs at the cap)."""
    import math
    import statistics as st
    from wattnot.proof.session import energy_clearly_down
    pairs = [(a, b) for a, b in zip(stock_j, cap_j) if a > 0]
    d = [100 * (b - a) / a for a, b in pairs]
    n = len(d)
    mean = st.mean(d) if d else float("nan")
    ci = _T975.get(n - 1, 2.0) * st.stdev(d) / math.sqrt(n) if n >= 2 else float("inf")
    cv = 100 * st.stdev(stock_j) / st.mean(stock_j) if len(stock_j) >= 2 and st.mean(stock_j) else float("nan")
    down = energy_clearly_down(d, mean, ci, cv)
    # the answer check already proved this cap (energy, quality, p99); a 4-request probe can only
    # overturn it when the cap is CLEARLY worse here: the 95% CI on the change sits above zero
    worse = n >= 2 and math.isfinite(ci) and mean - ci > 0
    verdict = "saving" if down else "worse" if worse else "inconclusive"
    return {"keep_cap": not worse, "verdict": verdict, "pairs": n, "mean_pct": round(mean, 2) if d else None,
            "ci95_half_pct": round(ci, 2) if math.isfinite(ci) else None,
            "stock_j_per_req": round(st.mean(a for a, _ in pairs), 1) if pairs else None,
            "cap_j_per_req": round(st.mean(b for _, b in pairs), 1) if pairs else None,
            "label": "MEASURED NVML energy per request, paired with the same request at stock"}


class TrafficRun:
    """One background run. ``send`` posts a chat body to the gateway; ``direct`` (optional) sends
    it straight to the upstream for the without arm; ``transcribe`` turns a wav into text."""

    def __init__(self, send, *, direct=None, transcribe=None, without_log: Path | None = None,
                 max_usd: float = 1.0, lc_runner=None, spent=None, calib_path: Path | None = None,
                 gpu=None, cap_w: int = 375, answer_check=None, event=None, result_path: Path | None = None):
        # gpu: NvmlGpu-shaped (energy_mj, limit_w, default_w, set_w); with it, side by side runs the
        # arms one after the other: without at stock power, then WattNot at the harness cap (375 W,
        # the H100 holdout winner) once the live answer check passes. answer_check(): AnswerCheck.
        self.gpu, self.cap_w, self.answer_check = gpu, int(cap_w), answer_check
        self.event = event or (lambda **kw: None)
        self.result_path = Path(result_path) if result_path else None
        self.calib_path = Path(calib_path) if calib_path else None
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

    def start(self, workload="mixed", n=60, speed="normal", with_without=False, base_url=None,
              mode="test") -> dict:
        """mode "live" (Go live, connected): every request goes through WattNot only, like
        production; savings are read against the stock baseline (ESTIMATE). The side-by-side
        without-WattNot arm exists only in mode "test" (Test on my prompts)."""
        if mode not in ("live", "test"):
            raise ValueError("mode must be live or test")
        if mode == "live" and with_without:
            raise ValueError("Go live sends all traffic through WattNot; side by side is only in Test on my prompts")
        self.base_url = base_url
        if self.thread and self.thread.is_alive():
            raise RuntimeError("traffic is already running")
        if int(n) not in (20, 60, 200):
            raise ValueError("requests must be 20, 60 or 200")
        if speed not in SPEEDS:
            raise ValueError(f"speed must be one of {list(SPEEDS)}")
        items = plan(workload, n)
        self._stop.clear()
        self.state.update(running=True, done=0, total=len(items), errors=0, usd=0.0, stopped=None, cap_rollback=None,
                          workload=workload, mode=mode, with_without=bool(with_without), without={"done": 0, "usd": 0.0},
                          seq_usd=0.0, run_id=None, finished_ts=None, sequential_result=None)
        target = self._run
        if with_without and self.direct and self.gpu is not None:
            target = self._run_sequential   # one GPU: arms one after the other, power measured per arm
            self.state["sequential"] = True
        self.thread = threading.Thread(target=target, args=(items, SPEEDS[speed], with_without), daemon=True)
        self.thread.start()
        return dict(self.state)

    def stop(self) -> None:
        self._stop.set()

    def _calib(self, arm: str, kind: str, usd: float) -> None:
        if not self.calib_path:
            return
        c = load_calib(self.calib_path)
        s = c.setdefault(arm, {}).setdefault(kind, [0.0, 0])
        s[0], s[1] = s[0] + usd, s[1] + 1
        self.calib_path.parent.mkdir(parents=True, exist_ok=True)
        self.calib_path.write_text(json.dumps(c))

    def _arm(self, name, items, gap, call) -> dict:
        """One arm, alone on the GPU: NVML energy counter read before and after (MEASURED)."""
        g = self.gpu
        from wattnot.gateway import monitor as _mon
        if name == "with":   # shadow calls to stock run alone and are taken out of this window
            _mon.SHADOW_METER.update(read=g.energy_mj, sync=True, excluded_mj=0, calls=0)
        self.event(step="arm_start", arm=name, cap_w=g.limit_w())
        at = f" at {g.limit_w()} W" if g.limit_w() else " (no power cap on this provider)"
        self.state["phase"] = ("without WattNot" if name == "without" else "with WattNot") + at
        t0, e0 = time.time(), g.energy_mj()
        texts, toks, usd, per_j, tools = [], 0, 0.0, [], []
        ctl = getattr(self, "_cap_ctl", None) if name == "with" else None
        for i, (kind, body) in enumerate(items):
            if self._stop.is_set():
                self.state["stopped"] = self.state.get("stopped") or "stopped by you"
                break
            spent = float(self.state.get("seq_usd") or 0) + usd
            cap = getattr(self, "seq_max_usd", None)   # opt-in: hosted runs bill per call
            if cap is not None and spent >= cap:
                self.state["stopped"] = f"hard stop: spend reached ${cap:.2f}"
                self._stop.set()
                break
            if kind == "voice":   # side by side used to send the raw {"wav": ...} item upstream
                body = self._voice_body(body)
            body = {"model": "muse-spark-1.3", **SAMPLING, **body}
            cls = item_class(kind, body)
            if ctl is not None:
                ctl.before(cls)
            ej, sj, sc = g.energy_mj(), _mon.SHADOW_METER["excluded_mj"], _mon.SHADOW_METER["calls"]
            try:
                r = call(body, i) or {}
            except Exception:  # noqa: BLE001
                self.state["errors"] += 1
                r = {}
            if getattr(g, "estimate", False):
                g.add(r.get("joules"))
            rj = (g.energy_mj() - ej - (_mon.SHADOW_METER["excluded_mj"] - sj)) / 1000
            # the gate shadow-checks every saver-on call while it is warming up: that is gate warm-up
            warm = name == "with" and _mon.SHADOW_METER["calls"] > sc
            per_j.append([cls, round(rj, 2)] + (["gate warm-up"] if warm else []))
            if ctl is not None:
                ctl.after(cls, i, rj, warmup=warm)
            texts.append(str(r.get("text") or ""))
            tools.append(_mon.tool_key(r.get("tool_calls")) or None)
            toks += int(r.get("tokens") or 0)
            usd += float(r.get("usd") or 0)
            self.state[name + "_done"] = i + 1
            self.state["done"] = i + 1             # live progress for the UI (it reads done/total)
            if name == "without":
                self.state["without"]["done"] = i + 1
                self.state["without"]["usd"] = round(usd, 6)
            if gap:
                time.sleep(gap)
        j = (g.energy_mj() - e0) / 1000
        sh_j, sh_n = _mon.SHADOW_METER["excluded_mj"] / 1000, _mon.SHADOW_METER["calls"]
        if name == "with":
            _mon.SHADOW_METER.update(read=None, sync=False, excluded_mj=0, calls=0)
            j -= sh_j
        setup = setup_split(per_j) if name == "with" else None
        if setup:
            j -= setup["joules"]
        out = {"requests": len(texts), "joules": round(j, 1),
               "shadow_excluded": {"calls": sh_n, "joules": round(sh_j, 1),
                                   "label": "MEASURED NVML energy of the gateway's shadow calls to stock, "
                                            "run one at a time and taken out of this arm"} if name == "with" else None, "seconds": round(time.time() - t0, 1),
               "t0": round(t0, 3), "t1": round(time.time(), 3),
               "cap_w": g.limit_w(), "tokens": toks, "usd": round(usd, 6),
               "label": getattr(g, "label", None) or "MEASURED (NVML energy counter over the arm's window)",
               "texts": texts,
               "tool_calls": tools,   # (name, canonical args) per request, for agent agreement
               "per_request_j": per_j,
               "gate_warmup": {"requests": sum(1 for p in per_j if len(p) > 2),
                               "joules": round(sum(p[1] for p in per_j if len(p) > 2), 1),
                               "label": "gate warm-up: saver-on requests the per-model gate shadow-checked "
                                        "(MEASURED, counted in this arm, left out of the cap probe)"}
               if name == "with" else None,
               "setup": setup,
               "joules_incl_setup": round(j + setup["joules"], 1) if setup else None}
        self.state["seq_usd"] = round(float(self.state.get("seq_usd") or 0) + usd, 6)
        self.event(step="arm_end", arm=name, **{k: v for k, v in out.items() if k != "texts"})
        return out

    def _run_sequential(self, items, gap, with_without) -> None:
        g = self.gpu
        stock = g.default_w()
        res: dict = {"mode": "sequential", "stock_w": stock, "harness_cap_w": self.cap_w,
                     "workload": self.state.get("workload"),
                     "run_id": f"seq-{time.strftime('%Y%m%d-%H%M%S')}-{self.state.get('workload')}"}
        cap_ok = bool(getattr(g, "can_cap", lambda: True)())
        res["cap_available"] = cap_ok
        try:
            if cap_ok:
                g.set_w(stock)
            res["without"] = wo = self._arm("without", items, gap, lambda b, i: self.direct(b))
            check = self.answer_check() if (self.answer_check and cap_ok) else None
            if not cap_ok:
                res["answer_check"] = {"accepted": False, "skipped": True, "why_not": [CAP_UNAVAILABLE],
                                       "note": "software savers only; the power-cap step is skipped"}
                self.state["cap_note"] = CAP_UNAVAILABLE
                self.event(step="cap_unavailable", note=CAP_UNAVAILABLE)
            elif check is not None:
                base = (f"answer check: {getattr(check, 'items', None) and len(check.items)} "
                        f"proof-v1 prompts, stock vs {self.cap_w} W (about 10-12 min, GPU busy)")
                self.state["phase"] = base
                t_chk = time.time()

                def _progress(what, base=base, t_chk=t_chk):
                    self.state["phase"] = f"{base} · {what} · {int(time.time() - t_chk) // 60} min in"
                if hasattr(check, "progress"):
                    check.progress = _progress
                check.stock()                      # stock answers at stock power
                g.set_w(self.cap_w)
                if hasattr(check, "warmup") and not getattr(check, "warm", None):
                    self.state["phase"] = f"{base} · GPU warm-up (not measured, about 5 min)"
                    wu = check.warmup()
                    self.event(step="gpu_warmup", **wu)
                    self.state["phase"] = base
                st = check.at(self.cap_w)
                res["answer_check"] = st
                self.event(step="quality_check", cap_w=self.cap_w, **{k: v for k, v in st.items() if k != "label"})
                if not st["accepted"]:
                    g.set_w(stock)
                    self.event(step="rolled_back", to_cap_w=stock, failed=st.get("why_not"))
            else:
                res["answer_check"] = {"accepted": False, "why_not": ["no live answer check: cap not applied"]}
            self._cap_ctl = CapController(self, wo.get("per_request_j") or [],
                                          cap_on=cap_ok and g.limit_w() != stock, stock=stock)
            res["with"] = w = self._arm("with", items, gap,
                                        lambda b, i: self.send(b, {"x-wattnot-run": f"sim-{i}", "x-wattnot-traffic": "simulated"}))
            res["cap_decision"] = self._cap_ctl.decisions
            self._cap_ctl = None
            wants = [EASY_ANS[EASY.index(b["messages"][-1]["content"])]
                     if k == "text" and b["messages"][-1]["content"] in EASY else None for k, b in items]
            for arm in (wo, w):
                scored = [score(t, x) for t, x in zip(arm["texts"], wants) if x is not None]
                arm["correct"] = sum(scored) if scored else None
                arm["scored"] = len(scored)
                arm["j_per_correct"] = round(arm["joules"] / arm["correct"], 1) if arm.get("correct") else None
            res["agreement"] = arm_agreement(w, wo)
            res["no_worse"] = (w.get("correct") or 0) >= (wo.get("correct") or 0)
            if wo["joules"]:
                res["energy_saved_pct"] = round(100 * (wo["joules"] - w["joules"]) / wo["joules"], 2)
                res["energy_label"] = "ESTIMATE" if getattr(g, "estimate", False) else "MEASURED"
            self.state["without"]["done"] = wo["requests"]
            self.state["done"] = w["requests"]
        except Exception as e:  # noqa: BLE001
            res["error"] = type(e).__name__
            self.state["errors"] += 1
        finally:
            try:
                if cap_ok:
                    g.set_w(stock)                 # always back to stock power
            finally:
                res["final_limit_w"] = g.limit_w()
                self.event(step="restored", cap_w=res["final_limit_w"])
                self.state["sequential_result"] = {k: ({kk: vv for kk, vv in v.items() if kk != "texts"}
                                                       if isinstance(v, dict) else v) for k, v in res.items()}
                if self.result_path:
                    self.result_path.parent.mkdir(parents=True, exist_ok=True)
                    self.result_path.write_text(json.dumps(res, indent=1))       # latest
                    with self.result_path.with_name("sequential.runs.jsonl").open("a") as fh:  # one per run, never overwritten
                        fh.write(json.dumps(res) + "\n")
                self.state["phase"] = "done"
                self.state["run_id"] = res.get("run_id")
                self.state["finished_ts"] = round(time.time(), 3)
                self.state["running"] = False

    def _voice_body(self, body: dict) -> dict:
        """A voice item is a wav path: transcribe it, then send the words as a chat message.
        Both arms of a side-by-side run send the same transcript, so cache it per wav."""
        if "messages" in body:
            return body
        wav = body.get("wav")
        cache = self.__dict__.setdefault("_voice_text", {})
        if wav not in cache:
            text = None
            if wav and self.transcribe:
                try:
                    text = self.transcribe(Path(wav).read_bytes())
                except Exception:  # noqa: BLE001
                    text = None
            cache[wav] = text
        return {"messages": [{"role": "user", "content": (cache[wav] or "What is 6 times 7?") + " Answer in one line."}]}

    def _run(self, items, gap, with_without) -> None:  # noqa: D401
        try:
            for i, (kind, body) in enumerate(items):
                if self._stop.is_set():
                    self.state["stopped"] = "stopped by you"
                    break
                if self.state["usd"] + self.state["without"]["usd"] >= self.max_usd:
                    self.state["stopped"] = f"hard stop: spend reached ${self.max_usd:.2f}"
                    break
                if kind == "voice":
                    body = self._voice_body(body)
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
                # both arms get the same sampling params (the holdout's temperature 0 / seed 1234 /
                # 1536-token budget), so any difference is a saver's doing, not sampling noise
                body = {"model": "muse-spark-1.3", **SAMPLING, **body}
                try:
                    rec = self.send(body, {"x-wattnot-run": f"sim-{i}", "x-wattnot-traffic": "simulated"})
                    self.state["usd"] += float((rec or {}).get("usd") or 0)
                    self._calib("with", kind, float((rec or {}).get("usd") or 0))
                except Exception:  # noqa: BLE001
                    self.state["errors"] += 1
                if with_without and self.direct:
                    try:
                        w = self.direct(body)
                        self.state["without"]["done"] += 1
                        self.state["without"]["usd"] += float(w.get("usd") or 0)
                        self._calib("without", kind, float(w.get("usd") or 0))
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


class CapController:
    """With arm only: the cap stays on for a workload class until CAP_PROBE_N of its requests have
    run at the cap; their MEASURED joules are paired with the same requests at stock (without arm).
    If energy is not clearly down, that class goes back to stock power for the rest of the arm."""

    def __init__(self, run, stock_per_req, *, cap_on: bool, stock: int):
        self.run, self.cap_on, self.stock = run, cap_on, stock
        self.stock_j = [j for _, j in stock_per_req]
        self.seen: dict = {}
        self.decisions: dict = {}

    def _want_w(self, cls):
        d = self.decisions.get(cls)
        return self.stock if (d and not d["keep_cap"]) else self.run.cap_w

    def before(self, cls):
        if not self.cap_on:
            return
        g = self.run.gpu
        w = self._want_w(cls)
        if g.limit_w() != w:
            g.set_w(w)

    def after(self, cls, i, joules, warmup=False):
        # the probe starts once the gate's warm-up for this class is over: warm-up requests (a
        # saver on and shadow-checked, possibly switched off after) would mix the saver into the cap
        if not self.cap_on or cls in self.decisions or warmup:
            return
        self.seen.setdefault(cls, []).append((i, joules))
        if len(self.seen[cls]) < CAP_PROBE_N:
            return
        idx = [k for k, _ in self.seen[cls]]
        d = cap_decision([self.stock_j[k] for k in idx if k < len(self.stock_j)],
                         [j for k, j in self.seen[cls] if k < len(self.stock_j)])
        self.decisions[cls] = d
        if d["keep_cap"]:
            d["message"] = (f"probe inconclusive, keeping checked cap for {cls} traffic (MEASURED)"
                            if d["verdict"] == "inconclusive" else
                            f"cap kept for {cls} traffic: energy per request down (MEASURED)")
            self.run.event(step="cap_kept", workload_class=cls, cap_w=self.run.cap_w, **d)
            if d["verdict"] == "inconclusive":
                self.run.state["cap_rollback"] = d["message"]
        else:
            d["message"] = f"cap rolled back for {cls} traffic: energy per request clearly up (MEASURED)"
            self.run.event(step="rolled_back", to_cap_w=self.stock, workload_class=cls,
                           failed=[d["message"]], **{k: v for k, v in d.items() if k != "message"})
            self.run.state["cap_rollback"] = d["message"]
