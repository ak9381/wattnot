"""Modal Spark Open top-k sprint, with a fully CPU-testable dry run.

The real sprint runs an 8xB200 vLLM server, sweeps the MoE top-k (8 stock, then 6 and
4), and after each top-k value writes a signed cloud receipt. It is driven entirely from
the local side: the remote Modal function only *serves* vLLM, and the laptop runs the tuner
(``wattnot.cloud.run``) against that endpoint, builds the receipt and signs it offline. That
keeps one rule absolute: **nothing in this file imports ``wattnot`` at module top** -- the
Modal container does not ship the package, and a top-level import crash-loops it (the proof
run saw exactly that). Every ``wattnot`` import lives inside a function.

Safety rails on every arm (see docs/spend-plan-98.md, "Modal $98"):
  * the shared spend ``Guard`` ($98 hard / $90 soft / $96 effective-hard) gates each arm;
  * ``cloud_timeout_s`` sets each Modal function's own ``timeout`` from the headroom left,
    so the cap holds even if the laptop dies;
  * a local ``kill_timer`` is the backstop next to that server-side timeout;
  * if the server is not answering by minute 15 the arm is stopped and results are saved;
  * the receipt (the arm's result) is written and signed before any stop or teardown;
  * one server restart per top-k value, plus a repeat of top-k 8 for the noise floor.

Stages (each is a separate, opt-in command; nothing runs on import):
  python modal/spark_topk_sprint.py --plan       # print the plan + cost, no Modal call ($0)
  python modal/spark_topk_sprint.py --dry-run DIR # CPU dry run: fake server + fake meter, $0
  modal run modal/spark_topk_sprint.py::prefetch    # CPU container -> Volume, NVFP4 533 GB
  modal run modal/spark_topk_sprint.py::power_probe # 1xT4 ~60 s: can we read NVML power?
  modal run modal/spark_topk_sprint.py::smoke       # 1xL4 Llama 3.2 1B, same code path
  modal run modal/spark_topk_sprint.py::sprint      # 8xB200 top-k 8/6/4 (+8 repeat), checkpointed

The real 8xB200 serve (``_serve_b200`` -> deploy + endpoint discovery) is the only paid GPU
path here and is gated by an explicit flag: it refuses, *before any Modal API call*, unless
``WATTNOT_MODAL_GO=1`` is set in the environment. Without it there is no deploy, no GPU and no
spend -- use ``--plan`` or ``--dry-run``.

The dry run exercises the whole local driver -- guard, timeouts, kill timer, serve/wait,
receipt build + offline signature -- with an in-process fake server and a fake billing
meter. It opens no socket, launches nothing on Modal and spends no money.
"""
from __future__ import annotations
import json, sys, time

MODEL = "meta-models/Muse-Spark-Open-816B-A42B-NVFP4"   # 533 GB (HF API, 29 Oct), Meta official
SMOKE_MODEL = "meta-llama/Llama-3.2-1B-Instruct"
TOPKS = (8, 6, 4)                                        # 8 = stock
REPEAT_K = 8                                             # re-run stock once for the noise floor
VOL, CKPT = "wattnot-spark-weights", "wattnot-spark-ckpt"
B200_8_USD_H = 52.0                                      # 8x$6.25 + CPU/mem, list price (ESTIMATE)
APP_NAME = "wattnot-spark-topk-draft"
SERVE_DEADLINE_S = 15 * 60                               # not serving by minute 15 -> stop the arm
ARM_CAP_S = 22 * 60                                      # load+warmup+measure budget per top-k arm
HARD_USD, SOFT_USD = 98.0, 90.0                          # effective hard is 96 (lag margin $2)
GO_ENV = "WATTNOT_MODAL_GO"                               # must be "1" to deploy/serve for real
SMOKE_ENV = "WATTNOT_MODAL_SMOKE"                          # "1" to also run the smoke arm in live mode
GPU_B200 = "B200:8"                                       # 8x B200, re-asserted at serve time
GPU_SMOKE = "L4"                                          # cheap single GPU for the 1B smoke arm (never B200)
SERVE_PORT = 8000                                         # vLLM OpenAI server port in the container

SCHEDULE = [  # (minute_from, minute_to, step, measured?)
    (0, 12, "container start, mount Volume, vLLM load NVFP4 TP=8 (cache warm)", False),
    (12, 15, "warmup + NVML power read check; stop the arm if not serving by minute 15", True),
    (15, 37, "top-k 8 (stock): 92-item tiers, J/correct, tok/s, p99, pass rate -> receipt", True),
    (37, 42, "restart with --hf-overrides num_experts_per_tok=6", False),
    (42, 62, "top-k 6: same tiers -> receipt", True),
    (62, 67, "restart with num_experts_per_tok=4", False),
    (67, 87, "top-k 4: same tiers -> receipt", True),
    (87, 90, "top-k 8 repeat (noise floor) + write summary, exit (container stops billing)", True),
]


def plan() -> dict:
    mins = SCHEDULE[-1][1]
    return {"model": MODEL, "topks": TOPKS, "repeat_k": REPEAT_K, "gpu": "B200:8",
            "minutes": mins, "usd_estimate": round(mins / 60 * B200_8_USD_H, 2),
            "hard_usd": HARD_USD, "soft_usd": SOFT_USD, "effective_hard_usd": HARD_USD - 2.0,
            "serve_deadline_min": SERVE_DEADLINE_S // 60, "arm_cap_min": ARM_CAP_S // 60,
            "schedule": [dict(zip(("from", "to", "step", "measured"), s)) for s in SCHEDULE],
            "label": "PLAN (nothing deployed)"}


def serve_args(topk: int, model: str = MODEL, tp: int = 8) -> list[str]:
    a = ["vllm", "serve", model, "--tensor-parallel-size", str(tp), "--max-model-len", "8192",
         "--enable-expert-parallel", "--gpu-memory-utilization", "0.90",
         "--download-dir", "/weights"]
    if topk != 8:
        a += ["--hf-overrides", json.dumps({"num_experts_per_tok": topk})]
    return a


def done_arms(ckpt: dict) -> list[int]:
    """Preemption resume: arms already written to the ckpt Volume are skipped."""
    return [k for k in TOPKS if ckpt.get(str(k), {}).get("status") == "done"]


LEDGER = "data/spend/modal.ledger.jsonl"
T4_USD_H = 0.59                                          # list price (ESTIMATE)


def guard_for(usd_h: float, ledger: str = LEDGER):
    """The one shared spend guard for every Modal arm: $98 hard / $90 soft, priced per GPU-second
    at the all-in rate. Imported here, locally only (the container has no wattnot)."""
    from pathlib import Path
    from wattnot.spend.guard import GPU_SECONDS, Guard, Ledger, Price
    return Guard("modal", Price(GPU_SECONDS, usd_per_s=usd_h / 3600), Ledger(Path(ledger)),
                 hard_usd=HARD_USD, soft_usd=SOFT_USD)


def stage_timeout(cap_s: int, usd_h: float, local: bool = True) -> int:
    """Every Modal function's timeout: the smaller of the stage cap and what the $98 spend guard
    still allows (cloud_timeout_s). wattnot is imported here, locally only: the container does
    not ship the package, so a top-level import would crash it."""
    if not local:
        return cap_s
    from wattnot.spend.guard import cloud_timeout_s
    t = min(cap_s, cloud_timeout_s(guard_for(usd_h)))
    if t <= 0:
        raise SystemExit("spend guard: no headroom left, not launching")
    return t


def kill_timer(seconds: int, app_name: str, *, grace_s: int = 60, stop=None, exit_=None):
    """Local backstop next to the server-side timeout: stop the app and exit when time is up.

    `stop`/`exit_` are injectable only so the firing path can be tested without actually
    stopping a Modal app or killing the process; in production both default to the real
    `modal app stop` call and `os._exit(3)`."""
    import os, subprocess, threading

    def _stop():
        subprocess.run(["modal", "app", "stop", app_name], timeout=60, check=False)

    def fire():
        (stop or _stop)()
        (exit_ or os._exit)(3)
    t = threading.Timer(seconds + grace_s, fire)
    t.daemon = True
    t.start()
    return t


# ---- the fake server + meter used by the CPU dry run ---------------------------------------

class FakeServer:
    """In-process stand-in for an OpenAI-compatible endpoint (Modal vLLM or hosted Baseten).

    It answers every request from the workload's own known-good answers, with a usage block
    whose reasoning size shrinks at cheaper thinking levels (and, for Modal, at lower top-k),
    so the tuner keeps a cheaper arm exactly as it would live. It opens no socket and bills
    nothing. It carries `provider`/`base_url`/`model`/`key_env` so the cloud receipt is written
    for the right host, and handles the planner ("propose") and judge ("judge|...") tags too.
    """

    THINK = {"low": 1, "medium": 2, "high": 4, "xhigh": 8}  # relative reasoning cost by effort

    def __init__(self, *, provider: str = "modal", topk: int = 8, model: str | None = None,
                 workload=None) -> None:
        from wattnot.cloud import run as rn
        from wattnot.runner.workload import load_workload
        self.provider = provider
        self.topk = topk
        self.base_url = (f"https://{APP_NAME}-topk{topk}.modal.local/v1" if provider == "modal"
                         else "https://fake.baseten.local/v1")
        self.model = model or (f"Muse-Spark-Open#topk{topk}" if provider == "modal"
                               else "meta-models/muse-spark-open")
        self.key_env = ("MODAL_PROXY_TOKEN_ID+MODAL_PROXY_TOKEN_SECRET" if provider == "modal"
                        else "BASETEN_METRICS_KEY")
        wl = load_workload(workload or rn.WORKLOAD)
        self._answer = {i.prompt: f"ANSWER: {i.meta.get('gold', '0')}" for i in wl.items}

    def __call__(self, req):
        if req.tag == "propose":
            return self._reply(json.dumps({"orders": {}}), req.body, "low")
        if req.tag.startswith("judge|"):
            return self._judge_reply(req.body)
        effort = req.body.get("reasoning_effort", "xhigh")
        task = req.body["messages"][-1]["content"]
        return self._reply(self._answer.get(task, "ANSWER: 0"), req.body, effort)

    def _reply(self, content: str, body: dict, effort: str):
        from wattnot.cloud.transport import Reply
        think = self.THINK.get(effort, 8) * 12 * self.topk // 8   # reasoning tokens, scaled by k
        prompt = max(1, sum(len(m.get("content", "")) for m in body.get("messages", [])) // 4)
        completion = max(1, len(content) // 4) + think
        usage = {"prompt_tokens": prompt, "completion_tokens": completion,
                 "prompt_tokens_details": {"cached_tokens": 0},
                 "completion_tokens_details": {"reasoning_tokens": think}}
        rb = {"model": self.model, "usage": usage,
              "choices": [{"message": {"content": content}, "finish_reason": "stop"}]}
        return Reply(200, rb, 0.15 + think / 400.0, {}, 0, 1_790_000_000.0)

    def _judge_reply(self, body: dict):
        from wattnot.cloud.transport import Reply
        payload = json.loads(body["messages"][-1]["content"])
        content = json.dumps({"verdicts": [{"id": r["id"], "verdict": "same"} for r in payload]})
        usage = {"prompt_tokens": max(1, len(body["messages"][-1]["content"]) // 4),
                 "completion_tokens": max(1, len(content) // 4),
                 "prompt_tokens_details": {"cached_tokens": 0}}
        rb = {"model": self.model, "usage": usage,
              "choices": [{"message": {"content": content}, "finish_reason": "stop"}]}
        return Reply(200, rb, 0.2, {}, 0, 1_790_000_000.0)


def fake_meter(seconds_by_k: dict | None = None):
    """A fake `modal billing report` meter for the dry run: returns GPU-dollars for reconcile,
    always a touch BELOW the ledger (the safe direction), so no arm is ever under-counted."""
    seconds_by_k = seconds_by_k or {}

    def report(topk: int) -> float:
        return round(seconds_by_k.get(topk, 0.0) * B200_8_USD_H / 3600 * 0.9, 6)
    return report


# ---- the shared arm driver (production and dry run both go through here) --------------------

def _topk_opts():
    from wattnot.cloud import run as rn
    return rn.Options()


def _require_go(action: str) -> None:
    """The one gate in front of every paid/GPU Modal path. Raises -- before any Modal API call --
    unless WATTNOT_MODAL_GO=1 is set. Keeps `modal run ... ::sprint` and `--dry-run` free by
    default: you only spend when you have explicitly opted in."""
    import os
    if os.environ.get(GO_ENV) != "1":
        raise SystemExit(
            f"spark_topk_sprint: refusing to {action} without {GO_ENV}=1. This starts a paid "
            f"GPU deploy. Export {GO_ENV}=1 to launch for real; otherwise use --plan or "
            f"--dry-run (both $0, no GPU).")


def _deploy_b200(topk, model, fn_timeout, *, gpu=GPU_B200, deploy=None, discover=None) -> str:
    """GO-gated real serve of the vLLM endpoint for one top-k value on `gpu`, returning its URL.

    Re-asserts the GPU spec (``GPU_B200`` for the real top-k arms, ``GPU_SMOKE`` for the 1B smoke
    arm -- so the smoke model never lands on an 8xB200 node) and sets this arm's server-side
    `timeout` (the guard's cloud_timeout_s) via ``with_options`` so the cap holds even if the
    laptop dies, carries the top-k, model and tensor-parallel size as a per-arm Secret (the
    container reads WATTNOT_SERVE_TOPK/MODEL/TP), deploys the app and discovers the web endpoint
    URL. `deploy`/`discover` are injectable for tests; in production they default to ``app.deploy``
    and ``Function.get_web_url``. The GO gate is checked *first*, before any Modal object is
    touched, so nothing on a GPU can start without the flag."""
    _require_go(f"deploy the {gpu} vLLM server for top-k {topk}")
    if app is None:
        raise SystemExit("spark_topk_sprint: the modal SDK is not installed; cannot serve.")
    tp = 8 if gpu == GPU_B200 else 1                     # single-GPU specs (e.g. the L4 smoke arm) run TP=1
    sec = modal.Secret.from_dict({"WATTNOT_SERVE_TOPK": str(topk), "WATTNOT_SERVE_MODEL": model,
                                  "WATTNOT_SERVE_TP": str(tp)})
    fn = b200_serve.with_options(gpu=gpu, timeout=int(fn_timeout), secrets=[sec])
    (deploy or (lambda: app.deploy(name=APP_NAME)))()
    url = (discover or fn.get_web_url)()
    if not url:
        raise SystemExit(f"spark_topk_sprint: no endpoint URL after deploy for top-k {topk}")
    return url


def _serve_b200(topk, model, fn_timeout, *, gpu=GPU_B200):  # pragma: no cover - needs Modal + money + GO flag
    """Production serve path: GO-gated deploy on `gpu` (8xB200 for real arms, L4 for the smoke
    arm), publish the discovered endpoint URL into MODAL_ENDPOINT_URL (so a ModalLiveTransport
    points at it), and return the arm's handle."""
    import os
    url = _deploy_b200(topk, model, fn_timeout, gpu=gpu)
    base = url if url.rstrip("/").endswith("/v1") else url.rstrip("/") + "/v1"
    os.environ["MODAL_ENDPOINT_URL"] = base
    return {"base_url": base, "topk": topk, "fn_timeout": int(fn_timeout)}


def _wait_serving(handle, clock, started, deadline_s):  # pragma: no cover - needs a live endpoint
    """DRAFT production wait: poll the endpoint's /models until it answers; return False if the
    deadline (minute 15) passes first so the caller can stop the arm and save results."""
    import urllib.request
    url = f"{handle['base_url']}/models"
    while clock() - started < deadline_s:
        try:
            with urllib.request.urlopen(url, timeout=5) as r:  # noqa: S310
                if r.status == 200:
                    return True
        except Exception:
            time.sleep(5)
    return False


def run_topk(topk: int, *, out_dir, transport, mode: str, guard, model: str = MODEL,
             sign_key=None, now=None, serve=None, wait_serving=None, meter=None,
             clock=None, make_timer=None, teardown=None, serve_deadline_s: int = SERVE_DEADLINE_S,
             arm_cap_s: int = ARM_CAP_S, opts=None) -> dict:
    """Run one top-k arm end to end: gate on the guard, set the server-side + local timeouts,
    start (restart) the server, wait until it serves, then tune and write a signed receipt.

    `serve`/`wait_serving`/`meter`/`make_timer` are injected by the dry run with fakes; in
    production they default to the real (DRAFT) Modal paths. The receipt is the arm's result
    and is written + signed by `wattnot.cloud.run.run` before this returns or any stop."""
    from pathlib import Path
    from wattnot.cloud import run as rn
    from wattnot.spend.guard import HardStop, cloud_timeout_s, finish

    clock = clock or time.monotonic
    make_timer = make_timer or kill_timer
    out_dir = Path(out_dir)
    guard.check_new_item()                                   # soft/hard cap gate before spending
    fn_timeout = cloud_timeout_s(guard)                      # server-side backstop for this arm
    if fn_timeout <= 0:
        raise HardStop(f"modal: no headroom to launch top-k {topk}")
    stage_cap = min(fn_timeout, arm_cap_s)
    timer = make_timer(stage_cap, APP_NAME)                  # local backstop next to the timeout
    started = clock()
    try:
        handle = (serve or _serve_b200)(topk, model, fn_timeout)
        if not isinstance(handle, dict):
            handle = {"base_url": getattr(transport, "base_url", ""), "handle": handle}
        url = handle.get("base_url") or ""
        if url and getattr(transport, "base_url", None) is not None:
            transport.base_url = url   # point the live transport at this arm's discovered endpoint
        serving = (wait_serving or _wait_serving)(handle, clock, started, serve_deadline_s)
        if not serving:
            finish(guard, "soft", f"top-k {topk}: not serving by minute "
                   f"{serve_deadline_s // 60}", [], out_dir, None,
                   teardown or (lambda: _teardown(handle)))
            return {"topk": topk, "serving": False, "receipt": None, "path": None,
                    "stopped": "not serving by deadline"}
        path, r = rn.run(opts or _topk_opts(), transport, mode, out_dir=out_dir, now=now,
                         sign_key=sign_key)
        if meter is not None:
            guard.reconcile(meter(topk), source="modal billing report (dry-run fake)")
        return {"topk": topk, "serving": True, "receipt": r, "path": path}
    finally:
        timer.cancel()


def _teardown(handle) -> list[str]:  # pragma: no cover - production stop path
    import subprocess
    subprocess.run(["modal", "app", "stop", APP_NAME], timeout=60, check=False)
    return []


def sprint(*, out_dir, make_transport, serve=None, wait_serving=None, guard=None, mode: str = "live",
           sign_key=None, now=None, topks=TOPKS, repeat_k: int | None = REPEAT_K,
           smoke_first: bool = True, smoke_gpu: str = GPU_SMOKE, meter=None, clock=None,
           make_timer=None, teardown=None) -> list[dict]:
    """Drive the whole sprint: optional smoke, one restart per top-k value, then a top-k 8
    repeat for the noise floor. Any soft/hard stop ends the sprint with results saved.

    Each arm carries its own GPU spec: the real top-k arms get ``GPU_B200`` (8xB200); the 1B
    smoke arm gets ``smoke_gpu`` (a cheap single GPU, L4 by default) so it is NEVER deployed on
    an 8xB200 node. The spec is bound only into the production ``_serve_b200`` path -- an injected
    fake ``serve`` (dry run, tests) is passed through untouched and keeps its (topk, model,
    fn_timeout) signature."""
    from wattnot.spend.guard import HardStop, SoftStop

    guard = guard or guard_for(B200_8_USD_H)
    base_serve = serve or _serve_b200

    def arm_serve(gpu):                                      # bind the per-arm GPU spec, production only
        if base_serve is _serve_b200:
            return lambda topk, model, fn_timeout: _serve_b200(topk, model, fn_timeout, gpu=gpu)
        return base_serve

    arms = []
    if smoke_first:
        arms.append(("smoke", SMOKE_MODEL, 8, smoke_gpu))    # cheap GPU, never B200
    arms += [("topk", MODEL, k, GPU_B200) for k in topks]    # one server restart per top-k value
    if repeat_k:
        arms.append(("repeat", MODEL, repeat_k, GPU_B200))   # noise floor
    from pathlib import Path

    out: list[dict] = []
    for idx, (kind, model, k, gpu) in enumerate(arms):
        arm_dir = Path(out_dir) / f"{idx}-{kind}-k{k}"     # one dir per arm: receipts never collide
        try:
            res = run_topk(k, out_dir=arm_dir, transport=make_transport(kind, model, k),
                           mode=mode, guard=guard, model=model, sign_key=sign_key, now=now,
                           serve=arm_serve(gpu), wait_serving=wait_serving, meter=meter, clock=clock,
                           make_timer=make_timer, teardown=teardown)
            out.append({"kind": kind, **res})
        except SoftStop as e:
            out.append({"kind": kind, "topk": k, "stopped": f"soft: {e}"})
            break
        except HardStop as e:
            out.append({"kind": kind, "topk": k, "stopped": f"hard: {e}"})
            break
    return out


def dry_run(out_dir, *, sign_key=None, now=None, guard=None) -> list[dict]:
    """CPU dry run of the Modal sprint: in-process fake server per arm + a fake billing meter.
    Nothing on Modal, no network, no money. Every arm writes a SIMULATED, signed receipt."""
    def make_transport(kind, model, k):
        return FakeServer(provider="modal", topk=k,
                          model=(SMOKE_MODEL if kind == "smoke" else f"Muse-Spark-Open#topk{k}"))

    return sprint(out_dir=out_dir, make_transport=make_transport,
                  serve=lambda topk, model, t: {"base_url": "", "started": True},
                  wait_serving=lambda h, clk, start, dl: True,
                  guard=guard or guard_for(B200_8_USD_H), mode="replay-synthetic",
                  sign_key=sign_key, now=now, meter=fake_meter(), teardown=lambda: [],
                  make_timer=lambda s, name: kill_timer(s, name, stop=lambda: None,
                                                        exit_=lambda code: None))


BASETEN_PLAN = (  # the three planned Baseten $98 runs (docs/spend-plan-98.md), minus calibration
    ("baseline", {"efforts": ["xhigh"]}),
    ("thinking-sweep", {"efforts": ["low", "medium", "high", "xhigh"]}),
    ("judge", {"judge": True}),
)


def baseten_dry_run(out_dir, *, sign_key=None, now=None) -> list[dict]:
    """CPU dry run of the Baseten $98 plan against a fake Baseten endpoint: baseline, the
    thinking-level sweep and the judge. Each writes a signed receipt that verifies and can be
    graded no higher than ESTIMATE (hosted energy is never MEASURED). No network, no money."""
    from pathlib import Path
    from wattnot.cloud import run as rn

    out = []
    for name, kw in BASETEN_PLAN:
        server = FakeServer(provider="baseten")
        path, r = rn.run(rn.Options(**kw), server, "replay-synthetic",
                         out_dir=Path(out_dir) / name, now=now, sign_key=sign_key)
        out.append({"name": name, "path": path, "receipt": r})
    return out


# ---- Modal definitions (only if the SDK is installed; never deployed from this branch) ------

try:
    import modal  # type: ignore
    _local = modal.is_local()
    app = modal.App(APP_NAME)
    image = (modal.Image.from_registry("vllm/vllm-openai:latest")
             .pip_install("huggingface_hub[hf_transfer]")
             .env({"HF_HUB_ENABLE_HF_TRANSFER": "1"})
             .add_local_dir("workloads", "/app/workloads")
             .add_local_dir("src", "/app/src"))
    weights = modal.Volume.from_name(VOL, create_if_missing=True)
    ckpt_vol = modal.Volume.from_name(CKPT, create_if_missing=True)

    PREFETCH_S = stage_timeout(4 * 3600, 0.5, _local)   # CPU container, about $0.5/h (ESTIMATE)
    PROBE_S = stage_timeout(300, T4_USD_H, _local)
    SMOKE_S = stage_timeout(ARM_CAP_S, 1.5, _local)     # 1xL4, about $1.5/h (ESTIMATE)

    @app.function(image=image, cpu=8, memory=16384, timeout=PREFETCH_S,
                  volumes={"/weights": weights}, secrets=[modal.Secret.from_name("hf")])
    def prefetch():
        from huggingface_hub import snapshot_download
        snapshot_download(MODEL, cache_dir="/weights", max_workers=16)
        weights.commit()

    @app.function(image=image, gpu="T4", timeout=PROBE_S)
    def power_probe():
        import subprocess
        return subprocess.run(["nvidia-smi", "--query-gpu=power.draw,power.limit,enforced.power.limit",
                               "--format=csv"], capture_output=True, text=True).stdout

    @app.function(image=image, gpu="L4", timeout=SMOKE_S, volumes={"/weights": weights})
    def smoke_serve():  # same serve code path as the real sprint, tiny model, cheap GPU
        import subprocess
        return subprocess.run(serve_args(8, SMOKE_MODEL, tp=1) + ["--dry-run"],
                              capture_output=True, text=True).stdout

    B200_SERVE_S = stage_timeout(ARM_CAP_S, B200_8_USD_H, _local)  # per-arm cap, guard-bounded

    @app.function(image=image, gpu=GPU_B200, timeout=B200_SERVE_S, max_containers=1,
                  volumes={"/weights": weights, "/ckpt": ckpt_vol},
                  secrets=[modal.Secret.from_name("hf")])
    @modal.concurrent(max_inputs=1000)
    @modal.web_server(SERVE_PORT, startup_timeout=SERVE_DEADLINE_S)
    def b200_serve():  # pragma: no cover - runs only on 8xB200 under WATTNOT_MODAL_GO=1
        # Serve the NVFP4 weights from the Volume. top-k and model arrive as a per-arm Secret
        # (WATTNOT_SERVE_TOPK / WATTNOT_SERVE_MODEL); _deploy_b200 re-deploys per top-k value.
        import os, subprocess
        topk = int(os.environ.get("WATTNOT_SERVE_TOPK", "8"))
        model = os.environ.get("WATTNOT_SERVE_MODEL", MODEL)
        tp = int(os.environ.get("WATTNOT_SERVE_TP", "8"))   # 8 on 8xB200; 1 on the single-GPU smoke arm
        subprocess.Popen(serve_args(topk, model, tp=tp) + ["--port", str(SERVE_PORT)])

    @app.local_entrypoint()
    def probe():
        kill_timer(PROBE_S, APP_NAME)
        print(power_probe.remote())

    @app.local_entrypoint()
    def smoke():
        kill_timer(SMOKE_S, APP_NAME)
        print(smoke_serve.remote())

    @app.local_entrypoint()
    def sprint_cli():  # pragma: no cover - deploys 8xB200, needs explicit go + money
        # The real sprint: the local driver tunes against the remote endpoint and signs each
        # receipt. Each arm's _serve_b200 deploys the 8xB200 endpoint for its top-k and sets
        # MODAL_ENDPOINT_URL; run_topk then points this transport at that URL. Refuses up front
        # without WATTNOT_MODAL_GO=1 so no deploy happens by accident.
        import os
        _require_go("run the 8xB200 top-k sprint")
        # The 1B smoke arm is skipped in live mode by default (it would otherwise take a paid GPU
        # for a throwaway check); set WATTNOT_MODAL_SMOKE=1 to include it -- it then runs on the
        # cheap L4 spec, never on 8xB200.
        smoke_first = os.environ.get(SMOKE_ENV) == "1"
        # Discover the (stable) endpoint URL once so the transport can be built before arm 0.
        os.environ["MODAL_ENDPOINT_URL"] = _serve_b200(TOPKS[0], MODEL,
                                                       B200_SERVE_S)["base_url"]
        from wattnot.cloud.transport import ModalLiveTransport
        res = sprint(out_dir="data/cloud/modal-sprint",
                     serve=_serve_b200, wait_serving=_wait_serving, smoke_first=smoke_first,
                     make_transport=lambda kind, model, k: ModalLiveTransport())
        print(json.dumps([{k: v for k, v in r.items() if k != "receipt"} for r in res], indent=1))

except (ImportError, AttributeError):  # SDK missing, or the repo modal/ dir shadows it
    app = None


if __name__ == "__main__":
    if "--plan" in sys.argv:
        print(json.dumps(plan(), indent=1))
    elif "--dry-run" in sys.argv:
        from pathlib import Path

        from wattnot.receipt.sign import generate_test_key
        i = sys.argv.index("--dry-run")
        out = sys.argv[i + 1] if i + 1 < len(sys.argv) else "data/cloud/dry-run"
        Path(out).mkdir(parents=True, exist_ok=True)
        key = generate_test_key(f"{out}/dry-key.pem") if "--sign" in sys.argv else None
        res = dry_run(out, sign_key=key)
        print(json.dumps([{"kind": r.get("kind"), "topk": r.get("topk"),
                           "serving": r.get("serving"),
                           "receipt": (r["receipt"]["receipt_id"] if r.get("receipt") else None)}
                          for r in res], indent=1))
