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
import json
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from wattnot.dash import experts, gpu

ROOT = Path(__file__).resolve().parents[3]
WEB = ROOT / "web" / "dash"
DATA = ROOT / "data" / "dash"
DEFAULT_BUNDLE = DATA / "h100-replay.json"
DEFAULT_CAPTURE = DATA / "granite-cpu-capture.jsonl.gz"


def _frame(event: str, obj, i: int | None = None) -> str:
    head = f"id: {i}\n" if i is not None else ""
    return f"{head}event: {event}\ndata: {json.dumps(obj, separators=(',', ':'))}\n\n"


def create_app(*, bundle: Path | None = None, capture: Path | None = None,
               follow: Path | None = None, results: Path | None = None) -> FastAPI:
    app = FastAPI(title="WattNot dashboard", version="1")
    app.add_middleware(GZipMiddleware, minimum_size=2000, compresslevel=3)
    cap_path = Path(follow or capture or DEFAULT_CAPTURE)
    following = follow is not None

    if results is not None:
        gpu_bundle = gpu.build_bundle(Path(results))
    else:
        gpu_bundle = json.loads(Path(bundle or DEFAULT_BUNDLE).read_text())

    @app.get("/api/dash/health")
    def health() -> dict:
        return {"ok": True, "following": following, "capture": cap_path.name,
                "capture_exists": cap_path.is_file(), "read_only": True}

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
