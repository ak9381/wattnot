"""The dashboard server: static pages, replay of real captures, and live following.

Read only. It never applies a change; approving a proposal stays in `wattnot proposals`.

    wattnot dash                        # replay the bundled real data on http://127.0.0.1:8765
    wattnot dash --follow cap.jsonl     # follow an expert capture while a local run writes it

Endpoints (all under /api/dash):

* ``GET /gpu``                      scenes, proposals, receipts (the bundled H100 replay)
* ``GET /gpu/scene/{id}/events``    SSE: one sample per event, paced by ``?speed=``
* ``GET /moe/meta``                 expert groups, vocab, energy pricing, texts
* ``GET /moe/events``               SSE of capture ticks; ``?speed=``; follows a growing file
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import shutil
import socket
import ssl
import time
import urllib.error
import urllib.request
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles

from wattnot.dash import experts, gpu, runplan

ROOT = Path(__file__).resolve().parents[3]
WEB = ROOT / "web" / "dash"
DATA = ROOT / "data" / "dash"
DEFAULT_BUNDLE = DATA / "h100-replay.json"
DEFAULT_CAPTURE = DATA / "granite-cpu-capture.jsonl.gz"
DEFAULT_STORY = DATA / "story.json"
META_HOST = "api.meta.ai"
OPEN_PATHS = ("/login", "/logout", "/api/dash/health", "/favicon.ico")
LOGIN_HTML = (
    "<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
    "<title>WattNot</title><style>body{margin:0;min-height:100vh;display:grid;place-items:center;"
    "background:#080B10;color:#E6ECF1;font:15px Inter,system-ui,sans-serif}form{display:grid;gap:14px;"
    "width:300px}h1{font-size:22px;font-weight:600;margin:0}p{margin:0;color:#8795A1}input,button{font:inherit;"
    "padding:10px 12px;border-radius:6px;border:1px solid #2C3945;background:#0C1118;color:inherit}"
    "button{border-color:#22C7D9;color:#22C7D9;cursor:pointer}.e{color:#F0504A;min-height:1.2em}"
    "</style><form method=post action=/login><h1>WattNot</h1><p>Enter the access token to open the demo.</p>"
    "<input name=token type=password autocomplete=current-password autofocus aria-label='access token'>"
    "<button>Open</button><div class=e>__ERR__</div></form>"
)


def _mac(token: str) -> str:
    return hmac.new(token.encode(), b"wattnot-dash", hashlib.sha256).hexdigest()


def meta_check(base: str | None = None, *, timeout: float = 6.0, opener=None) -> dict:
    """Is the Meta API reachable from this machine? Spends nothing: no model call is made.

    Steps: key present, DNS, TLS connect, then one GET on the models list. The key is used as a
    bearer only on that GET and is never returned.
    """

    from wattnot.meta.client import DEFAULT_BASE, KEY_ENV, load_key

    base = (base or os.environ.get("WATTNOT_META_BASE") or DEFAULT_BASE).rstrip("/")
    host = base.split("//", 1)[-1].split("/", 1)[0]
    key = load_key()
    steps = [{"id": "key", "ok": bool(key),
              "text": "API key found" if key else f"No API key set ({KEY_ENV} or the Muse login file)"}]
    out = {"checked_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "host": host, "steps": steps,
           "spends_money": False}

    def done(state, advice):
        out.update(state=state, ok=state == "ok", advice=advice)
        return out

    try:
        socket.getaddrinfo(host, 443)
        steps.append({"id": "dns", "ok": True, "text": f"{host} resolves"})
    except OSError as e:
        steps.append({"id": "dns", "ok": False, "text": f"{host} does not resolve ({e.__class__.__name__})"})
        return done("unreachable", "This network cannot look up the Meta API. Replay mode still works.")
    try:
        with socket.create_connection((host, 443), timeout=timeout) as s, \
                ssl.create_default_context().wrap_socket(s, server_hostname=host):
            steps.append({"id": "tls", "ok": True, "text": "secure connection opens"})
    except (OSError, ssl.SSLError) as e:
        steps.append({"id": "tls", "ok": False, "text": f"secure connection failed ({e.__class__.__name__})"})
        return done("blocked", "The connection is refused or dropped. Some data-center addresses are blocked by the API; "
                    "run the live agent from another network, or use replay mode.")
    req = urllib.request.Request(base + "/models", headers={"Accept": "application/json"})
    if key:
        req.add_header("Authorization", "Bearer " + key)
    try:
        if opener:
            code = opener(req, timeout)
        else:
            try:
                with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310
                    code = r.status
            except urllib.error.HTTPError as e:
                code = e.code
    except OSError as e:
        steps.append({"id": "api", "ok": False, "text": f"request failed ({e.__class__.__name__})"})
        return done("blocked", "The API did not answer. Try again, or use replay mode.")
    steps.append({"id": "api", "ok": code < 400 or code == 404, "text": f"API answered HTTP {code}"})
    if code in (401, 403):
        return done("key_rejected" if key else "key_missing",
                    "The API is reachable but did not accept this request. Check the key."
                    if key else "The API is reachable. Add a key to run the live agent.")
    if code >= 500 or code == 429:
        return done("degraded", "The API is reachable but busy or failing. Replay mode still works.")
    if not key:
        return done("key_missing", "The API is reachable. Add a key to run the live agent.")
    return done("ok", "Connected. The live agent can call Muse Spark.")


def _frame(event: str, obj, i: int | None = None) -> str:
    head = f"id: {i}\n" if i is not None else ""
    return f"{head}event: {event}\ndata: {json.dumps(obj, separators=(',', ':'))}\n\n"


def create_app(*, bundle: Path | None = None, capture: Path | None = None,
               follow: Path | None = None, results: Path | None = None,
               story: Path | None = None, token: str | None = None,
               events: Path | None = None, meta_probe=None,
               replay_only: bool | None = None) -> FastAPI:
    app = FastAPI(title="WattNot dashboard", version="1")
    token = token or os.environ.get("WATTNOT_DASH_TOKEN") or None
    # Hosted replay-only mode (for judges): no GPU, no Meta call of any kind, saved run file only.
    # WATTNOT_HOSTED_REPLAY=1 or --replay-only; ``events`` is then the saved run to replay.
    if replay_only is None:
        replay_only = os.environ.get("WATTNOT_HOSTED_REPLAY", "").lower() in ("1", "true", "yes", "on")
    if replay_only:
        follow = None  # a live capture is never followed in the hosted copy
    story_path = Path(story or DEFAULT_STORY)
    meta_cache: dict = {}

    if token:
        want = _mac(token)

        @app.middleware("http")
        async def gate(request: Request, call_next):
            path = request.url.path
            ok = hmac.compare_digest(request.cookies.get("wn_auth", "").encode(), want.encode()) or \
                hmac.compare_digest(request.headers.get("authorization", "")[7:].encode(), token.encode())
            if ok or path in OPEN_PATHS or path.startswith("/run/vendor"):
                return await call_next(request)
            if path.startswith("/api/"):
                return JSONResponse({"error": "sign in required", "login": "/login"}, status_code=401)
            return RedirectResponse("/login", status_code=303)

        @app.get("/login")
        def login_page(bad: int = 0):
            return HTMLResponse(LOGIN_HTML.replace("__ERR__", "That token did not match." if bad else ""))

        @app.get("/logout")
        def logout():
            r = RedirectResponse("/login", status_code=303)
            r.delete_cookie("wn_auth")
            return r

        @app.post("/login")
        async def login(request: Request):
            form = (await request.body()).decode()
            val = dict(x.split("=", 1) for x in form.split("&") if "=" in x).get("token", "")
            from urllib.parse import unquote_plus
            if hmac.compare_digest(unquote_plus(val).encode(), token.encode()):
                r = RedirectResponse("/", status_code=303)
                r.set_cookie("wn_auth", want, httponly=True, samesite="lax", max_age=86400)
                return r
            return RedirectResponse("/login?bad=1", status_code=303)

    app.add_middleware(GZipMiddleware, minimum_size=2000, compresslevel=3)
    cap_path = Path(follow or capture or DEFAULT_CAPTURE)
    following = follow is not None

    if results is not None:
        gpu_bundle = gpu.build_bundle(Path(results))
    else:
        gpu_bundle = json.loads(Path(bundle or DEFAULT_BUNDLE).read_text())

    @app.get("/api/dash/health")
    def health() -> dict:
        gpu_attached = shutil.which("nvidia-smi") is not None
        live = following or results is not None or events is not None
        return {"ok": True, "following": following, "capture": cap_path.name,
                "capture_exists": cap_path.is_file(), "read_only": True,
                "mode": "replay" if replay_only or not live else "live",
                "replay_only": replay_only, "meta_calls": not replay_only,
                "gpu_attached": False if replay_only else gpu_attached,
                "auth_required": bool(token), "story": story_path.is_file(),
                "events": events is not None, "events_name": events.name if events is not None else None}

    @app.get("/api/dash/story")
    def story_bundle():
        if not story_path.is_file():
            raise HTTPException(404, "story bundle not built: run scripts/build_dash_arch.py")
        return Response(story_path.read_bytes(), media_type="application/json")

    plan_cache: dict = {}

    @app.post("/api/dash/run/plan")
    async def run_plan(request: Request):
        """Re-evaluate a recorded run against the limits a person chose. Always a REPLAY; no GPU, no network."""
        if not story_path.is_file():
            raise HTTPException(404, "story bundle not built: run scripts/build_dash_arch.py")
        try:
            body = await request.json()
        except ValueError:
            raise HTTPException(400, "the request body must be JSON") from None
        if not isinstance(body, dict):
            raise HTTPException(400, "the request body must be a JSON object")
        mt = story_path.stat().st_mtime
        if plan_cache.get("mt") != mt:
            plan_cache.update(mt=mt, b=json.loads(story_path.read_text()))
        try:
            return runplan.plan(plan_cache["b"], body)
        except (ValueError, KeyError, TypeError) as e:
            raise HTTPException(400, str(e) if isinstance(e, ValueError) else "the run settings are not valid") from None

    @app.get("/api/dash/meta/health")
    def meta_health(fresh: int = 0):
        if replay_only:  # never touches the network: the hosted copy makes no Meta call
            return {"state": "replay_only", "ok": False, "called": False, "steps": [],
                    "advice": "This is a hosted replay. It makes no live Meta calls; the Muse Spark and "
                              "SAM results shown were saved from a real run.", "spends_money": False}
        now = time.time()
        if fresh or not meta_cache or now - meta_cache["t"] > 20:
            try:
                meta_cache.update(t=now, v=(meta_probe or meta_check)())
            except Exception as e:  # never a 500 for a diagnostics call
                meta_cache.update(t=now, v={"ok": False, "state": "error", "steps": [],
                                            "advice": f"The check itself failed: {e.__class__.__name__}.",
                                            "spends_money": False})
        return meta_cache["v"]

    @app.get("/api/dash/arch/live")
    async def arch_live(request: Request, speed: float = 1.0):
        """SSE of a run's events.jsonl as it grows; the page maps each type to a step."""
        if events is None:
            raise HTTPException(404, "no events file is being followed (start with --events FILE)")

        async def gen() -> AsyncIterator[str]:
            pos = 0
            while not await request.is_disconnected():
                try:
                    with events.open("rb") as f:
                        f.seek(pos)
                        chunk = f.read()
                        pos = f.tell()
                except OSError:
                    chunk = b""
                for line in chunk.decode("utf-8", "replace").splitlines():
                    if line.startswith("{"):
                        yield _frame("event", json.loads(line))
                        await asyncio.sleep(0.25 / max(speed, 0.1))
                yield ": keep-alive\n\n"
                await asyncio.sleep(0.5)

        return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    @app.get("/api/dash/gpu")
    def gpu_info() -> dict:
        slim = [{k: v for k, v in s.items() if k != "samples"} | {"n": len(s["samples"])}
                for s in gpu_bundle["scenes"]]
        return {**{k: v for k, v in gpu_bundle.items() if k != "scenes"}, "scenes": slim}

    @app.get("/api/dash/gpu/scene/{scene_id}")
    def gpu_scene(scene_id: str):
        for s in gpu_bundle["scenes"]:
            if s["id"] == scene_id:
                return s
        raise HTTPException(404, "no such scene")

    @app.get("/api/dash/gpu/scene/{scene_id}/events")
    async def gpu_events(scene_id: str, request: Request, speed: float = 20.0):
        scene = next((s for s in gpu_bundle["scenes"] if s["id"] == scene_id), None)
        if scene is None:
            raise HTTPException(404, "no such scene")
        start = int(request.headers.get("last-event-id", "-1")) + 1

        async def gen() -> AsyncIterator[str]:
            yield _frame("scene", {k: v for k, v in scene.items() if k != "samples"})
            for i, smp in enumerate(scene["samples"][start:], start):
                yield _frame("sample", smp, i)
                if speed > 0:
                    await asyncio.sleep(1.0 / speed)
            yield _frame("end", {"n": len(scene["samples"])})

        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache"})

    cache: dict = {}

    def _load() -> experts.Capture:
        """A finished capture is parsed once; one that is still being written, every time."""
        if not cap_path.is_file():
            raise HTTPException(404, "no capture yet")
        if following:
            return _partial(cap_path)
        key = (cap_path.stat().st_mtime_ns, cap_path.stat().st_size)
        if cache.get("key") != key:
            cap = experts.Capture.load(cap_path)
            cache.update(key=key, cap=cap, meta=experts.build_meta(cap, gpu_bundle.get("pricing")),
                         ticks=json.dumps(cap.ticks, separators=(",", ":")).encode())
        return cache["cap"]

    def _meta() -> dict:
        cap = _load()
        if following:
            return experts.build_meta(cap, gpu_bundle.get("pricing"))
        return cache["meta"]

    def _partial(path: Path) -> experts.Capture:
        """A capture that is still being written: read what is complete so far."""
        lines = []
        for raw in path.read_text().splitlines():
            try:
                lines.append(json.loads(raw))
            except json.JSONDecodeError:
                break
        return experts.Capture(lines)

    @app.get("/api/dash/moe/meta")
    def moe_meta() -> JSONResponse:
        return JSONResponse(_meta())

    @app.get("/api/dash/moe/ticks")
    def moe_ticks() -> JSONResponse:
        """Every tick at once, so the page can replay locally and flip adaptive top-k at will."""
        cap = _load()
        if following:
            return JSONResponse(cap.ticks)
        return Response(cache["ticks"], media_type="application/json")

    @app.get("/api/dash/moe/events")
    async def moe_events(request: Request, speed: float = 8.0):
        """Replays ticks paced by their recorded ``dt_s`` divided by ``speed`` (0 = at once).
        With ``--follow`` it keeps reading the file as the run appends to it."""
        if not cap_path.is_file():
            raise HTTPException(404, "no capture yet")

        async def gen() -> AsyncIterator[str]:
            pos, buf, n, idle = 0, "", 0, 0.0
            while True:
                if following:
                    with open(cap_path, "rb") as fb:
                        fb.seek(pos)
                        raw = fb.read()
                    pos += len(raw)
                    chunk = raw.decode("utf-8", errors="replace")
                else:
                    with experts.open_text(cap_path) as f:
                        chunk = f.read()
                buf += chunk
                lines = buf.split("\n")
                buf = lines.pop()
                for line in lines:
                    if not line.strip():
                        continue
                    d = json.loads(line)
                    idle = 0.0
                    if d["type"] == "tick":
                        yield _frame("tick", d, n)
                        n += 1
                        if speed > 0 and not following:
                            await asyncio.sleep(min(d.get("dt_s", 0.1), 1.0) / speed)
                        elif following:
                            await asyncio.sleep(0.02)
                    elif d["type"] == "end":
                        yield _frame("end", {"ticks": n})
                        return
                    elif d["type"] == "step.done":
                        yield _frame("step.done", d)
                if not following:
                    yield _frame("end", {"ticks": n})
                    return
                if await request.is_disconnected():
                    return
                idle += 0.5
                if idle > 600:
                    return
                yield ": keep-alive\n\n"
                await asyncio.sleep(0.5)

        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache"})

    @app.get("/")
    def index():
        return FileResponse(WEB / "index.html")

    fonts = ROOT / "web" / "run" / "vendor"
    if fonts.is_dir():  # the same self-hosted fonts the run page uses
        app.mount("/run/vendor", StaticFiles(directory=str(fonts)), name="fonts")
    app.mount("/", StaticFiles(directory=str(WEB), html=True), name="web")
    return app


def serve(host: str = "127.0.0.1", port: int = 8765, **kw) -> int:
    import uvicorn

    print(f"WattNot dashboard on http://{host}:{port}  (read only)", flush=True)
    uvicorn.run(create_app(**kw), host=host, port=port, log_level="warning")
    return 0


__all__ = ["create_app", "serve"]
