"""The dashboard server: static pages, replay of real captures, and live following.

Read-only, except the approval endpoints (`/api/dash/approve`, `/api/dash/reject`,
`/api/dash/undo/...`), which decide, apply and undo guardrail proposals. Power
caps are applied with a mock by default; `--live-apply` uses nvidia-smi on this
machine instead.

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

from wattnot.agentwaste.card import card as waste_card
from wattnot.agentwaste.card import card_v2 as waste_card_v2
from wattnot.agentwaste.card import card_v3 as waste_card_v3
from wattnot.agentwaste.card import load_repeat as load_waste_repeat
from wattnot.agentwaste.card import load_results as load_waste
from wattnot.agentwaste.card import load_v2 as load_waste_v2
from wattnot.agentwaste.card import load_v3 as load_waste_v3
from wattnot.approvals import model as _am
from wattnot.approvals.appliers import NvidiaSmiApplier, demo_appliers
from wattnot.approvals.guard import Guard
from wattnot.approvals.risk import load_config as load_guard_config
from wattnot.approvals.service import HTTP as _VIA_HTTP
from wattnot.approvals.service import Approvals, StateError
from wattnot.approvals.store import NotFound, resolve_root
from wattnot.dash import configure, experts, gpu, receiptview, run2, runplan

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


# take 4: a power check and a traffic run shared the GPU and reset each other's power limit
GPU_BUSY = "Another GPU job is running; wait for it to finish"

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
               replay_only: bool | None = None,
               approvals_dir: Path | None = None,
               auto_approve: bool = False,
               live_apply: bool = False,
               partners: Path | None = None,
               gateway: Path | None = None) -> FastAPI:
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
            if ok or path in OPEN_PATHS or path.startswith("/run/vendor") or path.startswith("/gw/"):
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

    @app.middleware("http")
    async def no_cache_static(request: Request, call_next):
        """Serve the static pages with Cache-Control: no-cache so an edited .html/.css/.js
        is always revalidated and a stale heuristic cache cannot pin old styling."""
        response = await call_next(request)
        path = request.url.path
        if path == "/" or path.endswith((".html", ".css", ".js")):
            response.headers["Cache-Control"] = "no-cache"
        return response

    app.add_middleware(GZipMiddleware, minimum_size=2000, compresslevel=3)
    cap_path = Path(follow or capture or DEFAULT_CAPTURE)
    following = follow is not None

    if results is not None:
        gpu_bundle = gpu.build_bundle(Path(results))
    else:
        gpu_bundle = json.loads(Path(bundle or DEFAULT_BUNDLE).read_text())

    def _run_events(run: str | None) -> Path | None:
        """A named round's events file (Modal/Baseten, by id from /api/launch/rounds), or None
        when that round has none recorded."""
        if not run:
            return None
        from wattnot.dash.launch import events_path_for
        p = events_path_for(run)
        return p if p is not None and p.is_file() else None

    @app.get("/api/dash/health")
    def health(run: str | None = None) -> dict:
        gpu_attached = shutil.which("nvidia-smi") is not None
        ev = _run_events(run) if run else events
        live = following or results is not None or ev is not None
        return {"ok": True, "following": following, "capture": cap_path.name,
                "capture_exists": cap_path.is_file(), "read_only": True,
                "mode": "replay" if replay_only or not live else "live",
                "replay_only": replay_only, "meta_calls": not replay_only,
                "gpu_attached": False if replay_only else gpu_attached,
                "auth_required": bool(token), "story": story_path.is_file(),
                "events": ev is not None, "events_name": ev.name if ev is not None else None}

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

    # ---- approve before apply ------------------------------------------------
    adir = Path(approvals_dir) if approvals_dir is not None else resolve_root(None)
    guard_cfg = load_guard_config(adir)
    use_live = bool(live_apply) and not replay_only
    if use_live:  # hosted replay mode always uses the mock, even with --live-apply
        approvals = Approvals(adir, appliers={"power_cap": NvidiaSmiApplier()})
    else:
        approvals = Approvals(adir, appliers=demo_appliers())
    guard = Guard(approvals, guard_cfg,
                  auto_approve_by="dashboard" if auto_approve else None)
    guard_mode = "live" if use_live else "mock"
    app.state.guard = guard
    app.state.approvals = approvals

    def _item(p: dict) -> dict:
        ch = p.get("change") or {}
        risk = ch.get("risk") or {}
        secs = None
        if p["status"] == "pending":
            secs = max(0.0, _am.parse_iso(p["expires_at"]) - time.time())
        dec = p.get("decision")
        appd = p.get("applied")
        return {
            "id": p["id"], "title": p.get("title"), "summary": p.get("summary"),
            "kind": p.get("kind"), "label": p.get("label"),
            "risk": {"level": risk.get("level"),
                     "reasons": list(risk.get("reasons") or [])},
            "change": {"before": ch.get("before"), "after": ch.get("after")},
            "status": p["status"], "created_at": p.get("created_at"),
            "expires_at": p.get("expires_at"), "seconds_left": secs,
            "decision": ({k: (dec or {}).get(k) for k in ("by", "via", "at", "note")}
                         if dec else None),
            "applied": ({"at": appd.get("at"), "by": appd.get("by")}
                        if appd else None),
            "applies_on_approve": (p.get("source") or {}).get("applier") != "orchestrate",
            "undo_available": p["status"] == "applied" and p.get("kind") in approvals.appliers,
            "content_sha256": p.get("content_sha256"),
            "planner": (p.get("source") or {}).get("planner"),
            "evidence": (p.get("receipt_preview") or {}).get("evidence"),
        }

    def _err(code: str, message: str, status: int) -> JSONResponse:
        return JSONResponse({"code": code, "message": message}, status_code=status)

    def _need_json(request: Request) -> JSONResponse | None:
        ctype = request.headers.get("content-type", "").split(";")[0].strip().lower()
        if ctype != "application/json":
            return _err("unsupported_media_type",
                        "send Content-Type: application/json", 415)
        return None

    @app.get("/api/dash/approvals")
    def approvals_list() -> dict:
        pending = [_item(p) for p in approvals.list(status="pending")]
        applied = [_item(p) for p in approvals.list(status="applied")]
        recent = [_item(p) for p in approvals.list()
                  if p["status"] not in ("pending", "applied")][:20]
        return {"pending": pending, "applied": applied, "recent": recent,
                "config": guard_cfg.to_dict(), "auto_approve": auto_approve,
                "mode": guard_mode, "now": _am.iso(time.time())}

    @app.post("/api/voiceguide")
    async def voiceguide(request: Request):
        """Voice Ask: Muse Voice Transcribe -> Muse Spark intent -> receipt-grounded answer.

        Live by default. WATTNOT_META_MOCK=1 is the explicit, labelled mock switch."""
        import base64 as _b64

        from wattnot.meta.client import load_key
        from wattnot.meta.voiceguide import process_voice_query

        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            body = {}
        body = body if isinstance(body, dict) else {}
        query = str(body.get("query") or body.get("text") or "")
        audio = None
        if not query and isinstance(body.get("audio_b64"), str):
            try:
                audio = _b64.b64decode(body["audio_b64"], validate=True)
            except Exception:  # noqa: BLE001
                audio = None
        # a hosted replay never calls Meta (health says meta_calls False), so Ask runs on the labelled mock
        mock = os.environ.get("WATTNOT_META_MOCK") == "1" or replay_only
        if not mock and not load_key():
            return JSONResponse({"intent": "answer", "label": "ERROR", "code": "no_key",
                                 "text": "Live Ask needs META_API_KEY (or ~/.config/muse/auth.json). "
                                         "No answer was generated."}, status_code=503)
        live = not mock
        try:
            out = await asyncio.to_thread(process_voice_query, query_text=query, audio_data=audio,
                                          live=live and bool(audio), mock=not live, spark_live=live)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"intent": "answer", "label": "ERROR", "code": "voice_failed",
                                 "text": f"Voice Ask failed ({type(exc).__name__}). No answer was generated."},
                                status_code=502)
        return JSONResponse(out)

    @app.get("/api/dash/matrix")
    def dash_matrix():
        """Run matrix: 12 rounds (PLANNED until a result file exists), Spark-vs-deterministic
        grades, Meta spend by model, provider credits. Read-only; no model call."""
        from wattnot.dash import matrix
        return matrix.build(root=Path(os.environ.get("WATTNOT_MATRIX_ROOT", str(ROOT))))

    @app.get("/api/dash/meta/spend")
    def dash_meta_spend():
        """Live Meta spend for the overview KPI, read from the ledger on every request (MEASURED).
        Read-only, no model call; an unreadable ledger answers available=false, never a 500."""
        from wattnot.meta.ledger import SpendLedger
        try:
            s = SpendLedger().summary()
        except Exception:  # noqa: BLE001
            return {"available": False, "label": "MEASURED", "source": "live Meta spend ledger"}
        return {"available": True, "label": "MEASURED", "source": "live Meta spend ledger",
                "total_usd": s["total_usd"], "calls": s["calls"], "cap_usd": s["cap_usd"],
                "soft_usd": s["soft_usd"], "remaining_usd": s["remaining_usd"]}

    @app.post("/api/dash/ask/card")
    async def ask_card(request: Request):
        """Ask about one dashboard card: Muse Spark explains it as text (no speech).
        Live by default; WATTNOT_META_MOCK=1 gives a labelled MOCK."""
        if (refused := _need_json(request)) is not None:
            return refused
        from wattnot.meta.askcard import explain_card
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            body = {}
        body = body if isinstance(body, dict) else {}
        try:
            out = await asyncio.to_thread(explain_card, body.get("card_id", ""), body.get("title", ""),
                                          body.get("text", ""))
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"label": "ERROR", "code": "ask_failed", "focus": "",
                                 "explanation": f"Ask failed ({type(exc).__name__}). No explanation was generated."},
                                status_code=502)
        return JSONResponse(out, status_code=503 if out.get("code") == "no_key" else 200)

    @app.post("/api/dash/approve")
    async def approvals_approve(request: Request):
        if (refused := _need_json(request)) is not None:
            return refused
        try:
            body = await request.json()
        except ValueError:
            return _err("bad_request", "the request body must be JSON", 400)
        if not isinstance(body, dict) or not body.get("id"):
            return _err("bad_request", "the request body needs an id", 400)
        by = body.get("by") or "dashboard:operator"
        try:
            p = approvals.approve(body["id"], by, via=_VIA_HTTP,
                                  note=body.get("note") or "",
                                  expect_sha256=body.get("sha256"))
        except NotFound:
            return _err("not_found", f"no proposal {body['id']}", 404)
        except StateError as e:
            return _err("conflict", str(e), 409)
        # a run that owns its change (orchestrate) applies it itself; applying here too would double-apply
        if body.get("apply") is True and (p.get("source") or {}).get("applier") != "orchestrate":
            try:
                p = guard.apply(p["id"], by=by, via=_VIA_HTTP)
            except StateError as e:
                return _err("conflict", str(e), 409)
        return _item(p)

    @app.post("/api/dash/reject")
    async def approvals_reject(request: Request):
        if (refused := _need_json(request)) is not None:
            return refused
        try:
            body = await request.json()
        except ValueError:
            return _err("bad_request", "the request body must be JSON", 400)
        if not isinstance(body, dict) or not body.get("id"):
            return _err("bad_request", "the request body needs an id", 400)
        try:
            p = approvals.reject(body["id"], body.get("by") or "dashboard:operator",
                                 via=_VIA_HTTP, note=body.get("note") or "")
        except NotFound:
            return _err("not_found", f"no proposal {body['id']}", 404)
        except StateError as e:
            return _err("conflict", str(e), 409)
        return _item(p)

    @app.post("/api/dash/undo/{change_id}")
    async def approvals_undo(change_id: str, request: Request):
        if (refused := _need_json(request)) is not None:
            return refused
        try:
            body = await request.json()
        except ValueError:
            body = {}
        if not isinstance(body, dict):
            body = {}
        try:
            p = guard.undo(change_id, body.get("by") or "dashboard:operator",
                            via=_VIA_HTTP, note=body.get("note") or "")
        except NotFound:
            return _err("not_found", f"no proposal {change_id}", 404)
        except StateError as e:
            return _err("conflict", str(e), 409)
        try:  # best effort: an undo beside a signed receipt gets a sidecar, never a failure
            receipt = (p.get("receipt") or {})
            if receipt.get("sha256"):
                from pathlib import Path as _Path
                dest = _Path(receipt["path"]).parent if receipt.get("path") else adir
                guard.write_addendum(change_id, dest)
        except Exception:  # noqa: BLE001 - never fail the undo
            pass
        return _item(p)

    partners_path = Path(partners) if partners is not None else DATA / "partners.json"

    @app.get("/api/dash/partners")
    def dash_partners():
        """Who is behind the run: a saved snapshot, so replay mode works too."""
        if not partners_path.is_file():
            raise HTTPException(404, "no partners snapshot yet: run scripts/build_partners.py")
        return Response(partners_path.read_bytes(), media_type="application/json")

    frameworks_path = ROOT / "data" / "frameworks" / "validation.json"

    @app.get("/api/dash/v2/frameworks")
    def dash_frameworks():
        """Framework drop-in validation: honest status per framework against the local mock.

        Written by scripts/validate_frameworks.py. Never a live upstream."""
        if not frameworks_path.is_file():
            raise HTTPException(404, "no framework validation yet: run scripts/validate_frameworks.py")
        return Response(frameworks_path.read_bytes(), media_type="application/json")

    @app.get("/api/dash/run/receipt")
    def run_receipt():
        """Trimmed view of the run's signed receipt, when one sits next to --events FILE."""
        if events is None:
            raise HTTPException(404, "no events file is being followed (start with --events FILE)")
        try:
            return receiptview.view_any(receiptview.load_any(receiptview.find_receipt(events)))
        except FileNotFoundError:
            raise HTTPException(404, "no signed receipt in this run folder yet") from None
        except (ValueError, OSError) as e:
            raise HTTPException(404, f"no valid receipt here: {e}") from None

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
    async def arch_live(request: Request, speed: float = 1.0, run: str | None = None):
        """SSE of a run's events.jsonl as it grows; the page maps each type to a step.

        ``run`` names a round from /api/launch/rounds (a Modal/Baseten run, say); without it
        this follows the server's own ``--events FILE``."""
        ev = _run_events(run) if run else events
        if ev is None:
            msg = ("No step events recorded for this run." if run
                   else "no events file is being followed (start with --events FILE)")
            raise HTTPException(404, msg)

        async def gen() -> AsyncIterator[str]:
            pos, buf = 0, ""
            while not await request.is_disconnected():
                try:
                    with ev.open("rb") as f:
                        f.seek(pos)
                        chunk = f.read()
                        pos = f.tell()
                except OSError:
                    chunk = b""
                buf += chunk.decode("utf-8", "replace")
                lines = buf.split("\n")
                buf = lines.pop()  # a chunk can split mid-line; hold the remainder until it completes
                for line in lines:
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

    # ---- v2 Configure / signed report ------------------------------------------
    def _story() -> dict:
        if not story_path.is_file():
            raise HTTPException(404, "story bundle not built: run scripts/build_dash_arch.py")
        mt = story_path.stat().st_mtime
        if plan_cache.get("mt") != mt:
            plan_cache.update(mt=mt, b=json.loads(story_path.read_text()))
        return plan_cache["b"]

    @app.post("/api/dash/v2/dataset/upload")
    async def dataset_upload(request: Request):
        """Validate and store a custom dataset (JSONL: prompt + expected or tests). Local only."""
        from wattnot import datasets as _ds
        raw = (await request.body()).decode("utf-8", errors="replace")
        if len(raw) > 5_000_000:
            raise HTTPException(413, "upload is over 5 MB")
        try:
            return _ds.save_custom(raw, Path(os.environ.get("WATTNOT_DATA_DIR", "runs")) / "datasets")
        except _ds.DatasetError as e:
            raise HTTPException(400, str(e)) from e

    @app.post("/api/dash/v2/dataset/decide")
    async def dataset_decide(request: Request):
        """Let WattNot decide from a sample of real prompts: {"prompts": [...]}."""
        from wattnot import datasets as _ds
        body = await request.json()
        try:
            d = _ds.decide([str(p) for p in (body.get("prompts") or [])])
            return {**d, "dataset": _ds.describe(d["chosen"])}
        except _ds.DatasetError as e:
            raise HTTPException(400, str(e)) from e

    @app.get("/api/dash/v2/preflight")
    def v2_preflight():
        """Read-only machine check, so the setup screen can grey out what this host can't do."""
        from wattnot import preflight as _pf
        from wattnot.settings import load as _load
        return {**_pf.run(), "mode": _load()["mode"]}

    @app.get("/api/dash/v2/options")
    def v2_options() -> dict:
        return configure.options(_story().get("config") or {})

    @app.post("/api/dash/v2/run")
    async def v2_run(request: Request):
        """Configure -> replay. Always REPLAY; a live choice is refused with 400."""
        try:
            body = await request.json()
        except ValueError:
            raise HTTPException(400, "the request body must be JSON") from None
        if not isinstance(body, dict):
            raise HTTPException(400, "the request body must be a JSON object")
        try:
            out = runplan.plan(_story(), configure.to_plan_body(body))
        except (ValueError, KeyError, TypeError) as e:
            raise HTTPException(400, str(e) if isinstance(e, ValueError) else "the run settings are not valid") from None
        out["choice"] = {k: v for k, v in body.items()
                         if k != "compare" and isinstance(v, (str, int, float, bool))}
        return out

    @app.get("/api/dash/v2/run2")
    def v2_run2() -> dict:
        """Headline RUN2 numbers for the before/after block. Read from the
        raw step file when the checkout has it, else a fixed constant."""
        return run2.load(ROOT)

    @app.get("/api/dash/v2/judge-path")
    def v2_judge_path() -> dict:
        """Proof tab bundle: Spark trace, gate explanation, fallback ledger,
        stack row, verify results. Prebuilt file first, else built now."""
        from wattnot.dash import judgepath
        f = ROOT / "data/judge/judge-path.json"
        return json.loads(f.read_text()) if f.exists() else judgepath.build(ROOT)

    @app.get("/api/dash/v2/runs")
    def v2_runs() -> dict:
        """Runs by run name: id, name, model, date."""
        return {"runs": run2.list_runs(ROOT)}

    @app.get("/api/dash/v2/runs/{run_id}")
    def v2_run_one(run_id: str) -> dict:
        """One run record holding both phases (baseline, then WattNot)."""
        rec = run2.get_run(ROOT, run_id)
        if rec is None:
            raise HTTPException(404, "no such run")
        return rec

    @app.get("/api/dash/v2/verify")
    def v2_verify() -> dict:
        """Run the real verifier on the bundled sample receipts: one genuine, one edited after signing."""
        from wattnot.verify import verify_receipt
        out = []
        for name in ("real-measured", "forged-edited"):
            f = ROOT / "verifier" / "samples" / f"{name}.json"
            if f.is_file():
                r = verify_receipt(f).to_dict()
                r["path"] = f"verifier/samples/{name}.json"
                r["sample"] = name
                out.append(r)
        return {"label": "SAMPLE", "note": "Sample receipts signed offline with the repo's test key. "
                "No Rekor entry, so the best grade is ESTIMATE.", "receipts": out}

    @app.get("/api/dash/agent-waste")
    def agent_waste():
        res = load_waste()
        if res is None:
            raise HTTPException(404, "no agent-waste results file")
        body = waste_card(res)
        v2 = load_waste_v2()
        if v2 is not None:
            body["second_layer"] = waste_card_v2(v2)
            v3 = load_waste_v3()
            if v3 is not None:
                body["second_layer"]["after_fixes"] = waste_card_v3(v3)
                rep = load_waste_repeat()
                if rep is not None:
                    body["second_layer"]["after_fixes"]["repeat"] = rep
        return body

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
        # The meta line may not be written yet; Capture() would raise StopIteration.
        if next((x for x in lines if x.get("type") == "meta"), None) is None:
            raise HTTPException(404, "capture has no meta line yet")
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

    from wattnot.dash.launch import router as _launch_router
    app.include_router(_launch_router())

    @app.get("/")
    def index():
        return FileResponse(WEB / "index.html")

    fonts = ROOT / "web" / "run" / "vendor"
    if fonts.is_dir():  # the same self-hosted fonts the run page uses
        app.mount("/run/vendor", StaticFiles(directory=str(fonts)), name="fonts")
    room_dir = WEB.parent / "room"  # the SAM room scan module: node.js and the saved scan folders
    if room_dir.is_dir():
        app.mount("/room", StaticFiles(directory=str(room_dir), html=True), name="room")
    @app.post("/api/report/eu/upload")
    async def eu_upload(request: Request, format: str = "json"):  # noqa: A002
        from wattnot.api.app import eu_upload_response
        return eu_upload_response(await request.body(), format)

    # The receipts API's read-only routes (list, one receipt, verify, EU report) on the same port,
    # so the dashboard's links work without a second server. Same handlers, same checks.
    from wattnot.api.app import create_app as _api_app
    api = _api_app(os.environ.get("WATTNOT_ROOT", str(ROOT)))
    have = {getattr(r, "path", None) for r in app.router.routes}
    for r in api.router.routes:
        path = getattr(r, "path", "")
        if path.startswith("/api/receipts") and path not in have and "GET" in (getattr(r, "methods", None) or ()):
            app.router.routes.append(r)

    _mount_plug(app, gateway)
    app.mount("/", StaticFiles(directory=str(WEB), html=True), name="web")
    return app


GATEWAY_UPSTREAM = None   # tests set a factory cfg -> upstream callable; None means the real upstream


class _GatewayHolder:
    """ASGI app at /gw: answers 503 until the gateway is started from the UI (or --gateway)."""

    def __init__(self) -> None:
        self.app, self.cfg, self.path, self.started = None, None, None, None

    def start(self, app, cfg: dict, path) -> None:
        self.app, self.cfg, self.path, self.started = app, cfg, path, time.time()
        self.ui_started = False

    def stop(self) -> None:
        self.app = None

    def status(self) -> dict:
        return {"running": self.app is not None, "ui_started": bool(getattr(self, "ui_started", False)), "config": str(self.path) if self.path else "defaults",
                "upstream": (self.cfg or {}).get("upstream", {}).get("base_url"),
                "savers_on": [k for k, v in ((self.cfg or {}).get("savers") or {}).items() if v.get("enabled")]}

    async def __call__(self, scope, receive, send):
        if self.app is None:
            from starlette.responses import JSONResponse as _J
            return await _J({"detail": "gateway not started: press Start gateway"}, status_code=503)(scope, receive, send)
        return await self.app(scope, receive, send)


def _snippets(base: str) -> dict:
    return {"base_url": base, "snippets": {
        "langchain": f'ChatOpenAI(base_url="{base}", api_key="unused", model="muse-spark-1.3")',
        "openai": f'from openai import OpenAI\nclient = OpenAI(base_url="{base}", api_key="unused")\n'
                  'client.chat.completions.create(model="muse-spark-1.3", messages=[{"role": "user", "content": "hi"}])',
        "curl": f"curl {base}/chat/completions -H 'Content-Type: application/json' "
                '-d \'{"model":"muse-spark-1.3","messages":[{"role":"user","content":"hi"}]}\''}}


def _mount_plug(app: FastAPI, gateway: Path | None) -> None:
    """Live plug routes: the gateway itself (/gw/v1, only with --gateway), the live feed, receipts,
    the EU fields and the deploy-config writer. Without a gateway the feed says REPLAY."""
    from fastapi import Body, HTTPException

    from wattnot.gateway import deploy as _deploy
    from wattnot.gateway import plug as _plug
    from wattnot.gateway.build import DEFAULTS, build_app, load_config

    cfg = load_config(gateway) if gateway else load_config(None)
    logs = (os.environ.get("WATTNOT_SAVINGS_LOG") or cfg["savings_log"],
            os.environ.get("WATTNOT_NODE_LOG") or cfg["node"]["log"])
    gw = _GatewayHolder()
    app.mount("/gw", gw, name="gateway")
    app.state.gateway_holder = gw
    saved_yaml = Path(os.environ.get("WATTNOT_PLUG_YAML") or "out/gateway/wattnot.yaml")

    def _start_gateway(path: Path | None) -> dict:
        c = load_config(path) if path else load_config(None)
        ov = os.environ.get("WATTNOT_PLUG_OVERLAY")   # site settings (tiers, upstream) on top of the UI choice
        if ov:
            from wattnot.gateway.build import _merge
            import yaml as _y
            c = _merge(c, _y.safe_load(Path(ov).read_text()) or {})
        c["savings_log"], c["node"]["log"] = logs
        gw.start(build_app(c, upstream=GATEWAY_UPSTREAM(c) if GATEWAY_UPSTREAM else None), c, path)
        return gw.status()

    if gateway:
        _start_gateway(Path(gateway))

    @app.get("/api/dash/plug")
    def plug_feed(last: int = 50) -> dict:
        out = _plug.feed(*logs, last=last)
        out["gateway_mounted"] = gw.app is not None
        out["traffic"] = traffic.state
        return out

    @app.get("/api/dash/plug/receipt/{rid}")
    def plug_receipt(rid: str) -> dict:
        r = _plug.receipt(logs[0], rid)
        if r is None:
            raise HTTPException(404, "no such receipt")
        return r

    @app.get("/api/dash/plug/receipt/{rid}/verify")
    def plug_receipt_verify(rid: str) -> dict:
        r = _plug.verify_receipt(logs[0], rid)
        if r is None:
            raise HTTPException(404, "no such receipt")
        return r

    @app.get("/api/dash/plug/quality")
    def plug_quality() -> dict:
        mon = None
        if gw.app is not None:
            try:
                st = gw.app.state.monitor.status()
                mon = {**st, "gate": gw.app.state.gate.status()}
            except AttributeError:
                mon = None
        return _plug.quality(logs[0], mon)

    @app.get("/api/dash/plug/eu")
    def plug_eu() -> dict:
        return _plug.eu_report(*logs)

    @app.post("/api/dash/deploy")
    def deploy(choice: dict = Body(...)) -> dict:
        try:
            return _deploy.render(choice)
        except (TypeError, ValueError) as e:
            raise HTTPException(400, str(e)) from e

    @app.post("/api/dash/deploy/save")
    def deploy_save(choice: dict = Body(...)) -> dict:
        """Write the choice to the saved wattnot.yaml the Start button uses. No keys are written."""
        try:
            r = _deploy.render(choice)
        except (TypeError, ValueError) as e:
            raise HTTPException(400, str(e)) from e
        saved_yaml.parent.mkdir(parents=True, exist_ok=True)
        saved_yaml.write_text(r["yaml"])
        return {"saved": str(saved_yaml)}

    @app.post("/api/dash/gateway/start")
    def gateway_start(request: Request) -> dict:
        try:
            _start_gateway(saved_yaml if saved_yaml.exists() else (Path(gateway) if gateway else None))
            gw.ui_started = True
            st = gw.status()
        except (OSError, ValueError) as e:
            raise HTTPException(400, f"could not start: {e}") from e
        return {**st, **_snippets(str(request.base_url).rstrip("/") + "/gw/v1")}

    @app.post("/api/dash/gateway/stop")
    def gateway_stop() -> dict:
        traffic.stop()
        gw.stop()
        return gw.status()

    @app.get("/api/dash/gateway/status")
    def gateway_status(request: Request) -> dict:
        return {**gw.status(), **_snippets(str(request.base_url).rstrip("/") + "/gw/v1")}

    from wattnot.gateway import traffic as _traffic

    def _send(body: dict, headers: dict) -> dict:
        if gw.app is None:
            raise RuntimeError("gateway is not started")
        from starlette.testclient import TestClient as _TC
        with _TC(gw.app) as cl:   # in-process HTTP into the running gateway
            r = cl.post("/v1/chat/completions", json=body, headers=headers)
        rid = r.headers.get("x-wattnot-receipt")
        rec = _plug.receipt(logs[0], rid) if rid else None
        try:
            text = str(r.json()["choices"][0]["message"].get("content") or "")
        except (ValueError, KeyError, IndexError, TypeError):
            text = ""
        a = (rec or {}).get("actual", {})
        return {"receipt_id": rid, "status": r.status_code, "usd": a.get("usd", 0), "text": text,
                "joules": a.get("joules"),
                "tokens": int(a.get("prompt_tokens") or 0) + int(a.get("completion_tokens") or 0)}

    def _direct(body: dict) -> dict:
        """The without-WattNot arm: same request, straight to the upstream at the baseline tier."""
        from wattnot.agentwaste.server import sanitize, tier_body
        from wattnot.gateway.build import http_upstream
        from wattnot.gateway.savings import _jpt, _usd
        c = gw.cfg or load_config(None)
        base = next(t for t in c["tiers"] if t["name"] == c["baseline"]["tier"])
        up = GATEWAY_UPSTREAM(c) if GATEWAY_UPSTREAM else http_upstream(c)
        clean, _, _ = sanitize(body)
        resp = up(tier_body(clean, base), {"x-wattnot-arm": "without"})
        u = resp.get("usage") or {}
        pin, pout = int(u.get("prompt_tokens") or 0), int(u.get("completion_tokens") or 0)
        try:
            text = str(resp["choices"][0]["message"].get("content") or "")
        except (KeyError, IndexError, TypeError, AttributeError):
            text = ""
        return {"model": base["model"], "prompt_tokens": pin, "completion_tokens": pout, "text": text,
                "tokens": pin + pout,
                "usd": round(_usd(base["model"], pin, pout), 7),
                "joules": round(_jpt(c, base["model"]) * (pin + pout), 2), "joules_label": "ESTIMATE"}

    def _transcribe(wav: bytes) -> str:
        from wattnot.spokenanswer.client import transcribe_wav_live
        return transcribe_wav_live(wav)

    without_log = Path(logs[0]).with_name(Path(logs[0]).stem + ".without.jsonl")
    def _spent() -> float:
        return round(sum(r["actual"]["usd"] for r in _plug._read(logs[0])
                         if str(r.get("run") or "").startswith("sim-") and r.get("ts", 0) >= traffic_t0[0]), 6)

    traffic_t0 = [0.0]
    calib_path = Path(logs[0]).with_name(Path(logs[0]).stem + ".traffic-calib.json")
    def _event(**kw):
        try:
            with open(logs[1], "a") as f:
                f.write(json.dumps({"kind": "event", "ts": time.time(), "label": "MEASURED", **kw}) + "\n")
        except OSError:
            pass

    traffic = _traffic.TrafficRun(_send, direct=_direct, transcribe=_transcribe, without_log=without_log, spent=_spent,
                                  calib_path=calib_path, event=_event,
                                  result_path=Path(logs[0]).with_name("sequential.json"))

    def _attach_gpu():
        """GPU helper connected (node.enabled) and NVML present: side by side runs sequentially."""
        c = gw.cfg or cfg
        if traffic.gpu is not None or not (c.get("node") or {}).get("enabled") or os.environ.get("WATTNOT_NO_NVML"):
            return
        try:
            g = _traffic.NvmlGpu()
        except Exception:  # noqa: BLE001 - no NVML: stays concurrent, energy ESTIMATE
            if (c.get("node") or {}).get("meter") == "estimate":   # hosted API: sequential, ESTIMATE energy
                traffic.gpu, traffic.answer_check = _traffic.EstimateMeter(), None
            return
        if not getattr(g, "can_cap", lambda: True)():   # hosted GPU (Modal): NVML energy MEASURED, savers only, no cap step
            traffic.gpu, traffic.answer_check = g, None
            return
        from wattnot.node.answercheck import HarnessCapCheck, harness_asker

        class _A:   # NVML-shaped view for AnswerCheck
            def nvmlDeviceGetTotalEnergyConsumption(self, _h):
                return g.energy_mj()

            def nvmlDeviceGetPowerManagementLimit(self, _h):
                return g.n.nvmlDeviceGetPowerManagementLimit(g.h)

            def nvmlDeviceGetPowerUsage(self, _h):
                return g.n.nvmlDeviceGetPowerUsage(g.h)
        traffic.gpu = g
        traffic.cap_w = int((c.get("node") or {}).get("cap_max_w", 375))
        n = int((c.get("node") or {}).get("check_prompts", 111))
        traffic.answer_check = lambda: HarnessCapCheck(harness_asker(c), _A(), g.set_w, g.default_w(), n=n)

    def _est(body: dict) -> dict:
        from urllib.parse import urlparse
        host = urlparse(((gw.cfg or cfg).get("upstream") or {}).get("base_url", "")).hostname or ""
        own = host in ("localhost", "127.0.0.1", "::1") or host.startswith(("10.", "192.168."))
        return _traffic.estimate(body.get("workload", "mixed"), int(body.get("n", 60)), bool(body.get("with_without")),
                                 calib=_traffic.load_calib(calib_path), self_hosted=own)
    app.state.traffic = traffic
    if os.environ.get("WATTNOT_SEQ_MAX_USD"):   # hosted providers: hard $ stop across both arms
        traffic.seq_max_usd = float(os.environ["WATTNOT_SEQ_MAX_USD"])

    @app.post("/api/dash/traffic/estimate")
    def traffic_estimate(body: dict = Body(default={})) -> dict:
        try:
            return _est(body)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e

    @app.post("/api/dash/traffic/start")
    def traffic_start(request: Request, body: dict = Body(default={})) -> dict:
        nt = node_job["thread"]
        if nt is not None and nt.is_alive():   # one GPU job at a time: the power check owns the GPU
            raise HTTPException(409, GPU_BUSY)
        if gw.app is None:
            raise HTTPException(409, "Start the gateway first (step 3).")
        est = _est(body)
        if not est["under_1_usd"] and not body.get("confirm_over_1"):
            raise HTTPException(402, f"estimated ${est['usd']} is over $1; confirm to run")
        try:
            if not traffic.state.get("running"):
                traffic_t0[0] = time.time()
            _attach_gpu()
            return traffic.start(body.get("workload", "mixed"), int(body.get("n", 60)), body.get("speed", "normal"),
                                 bool(body.get("with_without")), mode=body.get("mode", "live"),
                                 base_url=str(request.base_url).rstrip("/") + "/gw/v1")
        except (RuntimeError, ValueError) as e:
            raise HTTPException(400, str(e)) from e

    @app.post("/api/dash/traffic/stop")
    def traffic_stop() -> dict:
        traffic.stop()
        return traffic.state

    @app.get("/api/dash/traffic/status")
    def traffic_status() -> dict:
        w = _plug._read(without_log)
        return {**traffic.state, "without_totals": {
            "requests": len(w), "usd": round(sum(x.get("usd", 0) for x in w), 6),
            "wh": round(sum(x.get("joules", 0) for x in w) / 3600, 4), "label": "ESTIMATE"}}

    node_job: dict = {"thread": None, "result": None}

    @app.post("/api/dash/node/run")
    def node_run(body: dict = Body(default={})) -> dict:
        """Add the node agent: rack check, then (with approve) the cap sweep. One job at a time."""
        import threading

        from wattnot.node import agent as _agent
        t = node_job["thread"]
        if t is not None and t.is_alive():
            raise HTTPException(409, GPU_BUSY)
        if body.get("approve") and traffic.state.get("running"):   # traffic is using the GPU
            raise HTTPException(409, GPU_BUSY)
        ncfg = load_config(gateway) if gateway else load_config(None)
        ov = os.environ.get("WATTNOT_PLUG_OVERLAY")
        if ov:
            from wattnot.gateway.build import _merge
            import yaml as _y
            ncfg = _merge(ncfg, _y.safe_load(Path(ov).read_text()) or {})
        ncfg["node"]["approval"] = "manual"

        def job() -> None:
            try:
                from wattnot.node.answercheck import HarnessCapCheck, harness_asker
                from wattnot.node.simgpu import SimulatedH100, open_gpu
                gpu = open_gpu()
                chk = None
                if not isinstance(gpu, SimulatedH100) and ncfg.get("upstream"):
                    # same check the backend sequential run uses: proof-v1, harness graders, sweep rule
                    chk = HarnessCapCheck(
                        harness_asker(ncfg), gpu,
                        lambda w: gpu.nvmlDeviceSetPowerManagementLimit(None, int(w) * 1000),
                        gpu.nvmlDeviceGetPowerManagementLimit(None) // 1000,
                        n=int((ncfg.get("node") or {}).get("check_prompts", 111)))
                node_job["result"] = _agent.run(ncfg, approve=bool(body.get("approve")), log_path=logs[1],
                                                sleep_s=float(body.get("sleep_s", 0.25)), gpu=gpu,
                                                answer_check=chk)
            except Exception as e:  # noqa: BLE001
                node_job["result"] = {"status": "error", "why": f"{type(e).__name__}: {e}"[:300]}
        node_job["result"] = None
        node_job["thread"] = threading.Thread(target=job, daemon=True)
        node_job["thread"].start()
        return {"started": True}

    @app.get("/api/dash/node/status")
    def node_status() -> dict:
        t = node_job["thread"]
        return {"running": bool(t and t.is_alive()), "result": node_job["result"]}

    _ = DEFAULTS


def serve(host: str = "127.0.0.1", port: int = 8765, *,
          approvals_dir: Path | None = None, auto_approve: bool = False,
          live_apply: bool = False, partners: Path | None = None, **kw) -> int:
    import uvicorn

    print(f"WattNot dashboard on http://{host}:{port}  "
          f"(approvals: {'live' if live_apply else 'mock'})", flush=True)
    uvicorn.run(create_app(approvals_dir=approvals_dir, auto_approve=auto_approve,
                           live_apply=live_apply, partners=partners, **kw),
                 host=host, port=port, log_level="warning")
    return 0


__all__ = ["create_app", "serve"]
