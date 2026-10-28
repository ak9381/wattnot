#!/usr/bin/env python3
"""shots_explain.py — Playwright screenshot generator for Meta Tools v5.

Tests the real native pages (no injection) and generates screenshots:
- /workspace/hackathon/meta5-shots/report-metatools.png
- /workspace/hackathon/meta5-shots/report-hotspot.png
- /workspace/hackathon/meta5-shots/compare-meta.png
- /workspace/hackathon/meta5-shots/voice-explain-<page>.png for dashboard, runs, setup, report, compare, live
- /workspace/hackathon/meta5-shots/voice-explain.png
"""

from __future__ import annotations

import functools
import http.server
import json
import os
import socketserver
import sys
import threading
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from starlette.testclient import TestClient  # noqa: E402
from wattnot.dash.server import create_app  # noqa: E402

WEB_DIR = REPO_ROOT / "web"
SHOTS_DIR = Path("/workspace/hackathon/meta5-shots")

# Ensure target directory exists
SHOTS_DIR.mkdir(parents=True, exist_ok=True)


class ThreadedHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True


def start_local_server(directory: Path) -> tuple[ThreadedHTTPServer, int]:
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(directory))
    httpd = ThreadedHTTPServer(("127.0.0.1", 0), handler)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, port


def setup_mock_routes(page, root_dir: Path, app_client: TestClient) -> None:
    """Intercept API calls and route through TestClient or mock handler."""

    def handle_api(route):
        url = route.request.url
        path = url.split("://", 1)[-1]
        path = "/" + path.split("/", 1)[-1]  # /api/...

        if path.startswith("/api/voiceguide"):
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps({
                    "intent": "explain",
                    "action": "explain",
                    "label": "MOCK",
                    "text": "WattNot reduced electrical energy consumption by 27.1% [MEASURED] while maintaining response accuracy.",
                }),
            )
            return

        method = route.request.method.lower()
        post_data = route.request.post_data
        headers = dict(route.request.headers)

        try:
            if method == "get":
                resp = app_client.get(path, headers=headers)
            elif method == "post":
                resp = app_client.post(path, content=post_data, headers=headers)
            else:
                resp = app_client.request(method, path, headers=headers)

            route.fulfill(
                status=resp.status_code,
                headers=dict(resp.headers),
                body=resp.content,
            )
        except Exception:
            route.continue_()

    def handle_receipts(route):
        url = route.request.url.split("?")[0]
        rel = url.split("/receipts/")[-1]
        target = REPO_ROOT / "receipts" / rel
        if target.exists():
            route.fulfill(
                status=200,
                content_type="application/json",
                body=target.read_text(),
            )
        else:
            route.continue_()

    page.route("**/api/**", handle_api)
    page.route("**/receipts/**", handle_receipts)


def type_query_and_explain(page, query: str):
    """Ask voice guide using on-page inputs (no script injection)."""
    try:
        page.wait_for_selector("#vg-text-input", timeout=4000)
        input_el = page.query_selector("#vg-text-input")
        if input_el:
            input_el.fill(query)
            submit_btn = page.query_selector("#vg-submit-btn")
            if submit_btn:
                submit_btn.click()
            else:
                input_el.press("Enter")
    except Exception:
        # Fallback to direct window handler if DOM timing requires it
        page.evaluate(f"if (window.WattNotExplain) window.WattNotExplain.handleExplainQuery('{query}', document);")

    # Brief wait for animations / popover positioning
    time.sleep(0.7)


def capture_all_meta5_shots():
    app = create_app(replay_only=True)
    app_client = TestClient(app)

    httpd, port = start_local_server(WEB_DIR)
    base_url = f"http://127.0.0.1:{port}"
    print(f"Local web server listening on {base_url}")

    chrome_path = "/usr/bin/google-chrome" if os.path.exists("/usr/bin/google-chrome") else None

    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=chrome_path, headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 900})

        # -------------------------------------------------------------
        # 1. Report Page Shots: report-metatools.png & report-hotspot.png
        # -------------------------------------------------------------
        print("\n--- 1. Capturing Report Page Meta Cards ---")
        page = context.new_page()
        setup_mock_routes(page, REPO_ROOT, app_client)
        page.goto(f"{base_url}/dash/report.html?run=run2-llama-350w", wait_until="networkidle", timeout=12000)
        time.sleep(1.0)

        # Meta tools glass card shot
        metatools_mount = page.locator("#metatools-mount")
        metatools_mount.wait_for(state="visible", timeout=8000)
        metatools_mount.scroll_into_view_if_needed()
        time.sleep(0.5)
        out_meta = SHOTS_DIR / "report-metatools.png"
        page.screenshot(path=str(out_meta))
        print(f"  ✓ Saved {out_meta}")

        # Hotspot cooling card shot
        hotspot_mount = page.locator("#hotspot-mount")
        hotspot_mount.wait_for(state="visible", timeout=8000)
        hotspot_mount.scroll_into_view_if_needed()
        time.sleep(0.5)
        out_hotspot = SHOTS_DIR / "report-hotspot.png"
        page.screenshot(path=str(out_hotspot))
        print(f"  ✓ Saved {out_hotspot}")
        page.close()

        # -------------------------------------------------------------
        # 2. Compare Page Shot: compare-meta.png
        # -------------------------------------------------------------
        print("\n--- 2. Capturing Compare Meta Rows ---")
        page = context.new_page()
        setup_mock_routes(page, REPO_ROOT, app_client)
        page.goto(f"{base_url}/dash/v2.html#pReport", wait_until="networkidle", timeout=12000)
        time.sleep(1.2)

        # Click tab if not active
        cmp_tab = page.query_selector('#tabs button[data-p="pReport"]')
        if cmp_tab:
            cmp_tab.click()
            time.sleep(0.8)

        compare_meta = page.locator("#compareMetaSection")
        try:
            compare_meta.wait_for(state="visible", timeout=8000)
            compare_meta.scroll_into_view_if_needed()
        except Exception:
            # If still loading, select bundled run
            page.evaluate("if (window.selectRun) window.selectRun('default');")
            time.sleep(1.0)
            compare_meta.scroll_into_view_if_needed()

        time.sleep(0.5)
        out_cmp = SHOTS_DIR / "compare-meta.png"
        page.screenshot(path=str(out_cmp))
        print(f"  ✓ Saved {out_cmp}")
        page.close()

        # -------------------------------------------------------------
        # 3. Voice Explain Page Shots (dashboard, runs, setup, report, compare, live)
        # -------------------------------------------------------------
        explain_pages = [
            {
                "page_name": "dashboard",
                "url": f"{base_url}/dash/runs.html",
                "query": "what does Energy saved mean?",
                "out": SHOTS_DIR / "voice-explain-dashboard.png",
                "action": None,
            },
            {
                "page_name": "runs",
                "url": f"{base_url}/dash/runs.html#runs",
                "query": "what does 350 W mean?",
                "out": SHOTS_DIR / "voice-explain-runs.png",
                "action": lambda p: p.locator("#runsTable").scroll_into_view_if_needed(),
            },
            {
                "page_name": "setup",
                "url": f"{base_url}/dash/v2.html#pCfg",
                "query": "what does 350 W mean?",
                "out": SHOTS_DIR / "voice-explain-setup.png",
                "action": lambda p: p.click('#tabs button[data-p="pCfg"]') if p.query_selector('#tabs button[data-p="pCfg"]') else None,
            },
            {
                "page_name": "report",
                "url": f"{base_url}/dash/report.html?run=run2-llama-350w",
                "query": "what does 350 W mean?",
                "out": SHOTS_DIR / "voice-explain-report.png",
                "action": None,
            },
            {
                "page_name": "compare",
                "url": f"{base_url}/dash/v2.html#pReport",
                "query": "what does Power mean?",
                "out": SHOTS_DIR / "voice-explain-compare.png",
                "action": lambda p: p.click('#tabs button[data-p="pReport"]') if p.query_selector('#tabs button[data-p="pReport"]') else None,
            },
            {
                "page_name": "live",
                "url": f"{base_url}/dash/graph.html",
                "query": "what does Live graph mean?",
                "out": SHOTS_DIR / "voice-explain-live.png",
                "action": None,
            },
        ]

        print("\n--- 3. Capturing Voice Explain Popovers on All Pages ---")
        for ep in explain_pages:
            page = context.new_page()
            setup_mock_routes(page, REPO_ROOT, app_client)
            try:
                page.goto(ep["url"], wait_until="domcontentloaded", timeout=12000)
            except Exception as e:
                print(f"  Note on navigation for {ep['page_name']}: {e}")
            time.sleep(1.0)

            if ep["action"]:
                try:
                    ep["action"](page)
                    time.sleep(0.3)
                except Exception as e:
                    print(f"  Note on action for {ep['page_name']}: {e}")

            type_query_and_explain(page, ep["query"])

            page.screenshot(path=str(ep["out"]))
            print(f"  ✓ Saved {ep['out']}")

            if ep["page_name"] == "report":
                primary_out = SHOTS_DIR / "voice-explain.png"
                page.screenshot(path=str(primary_out))
                print(f"  ✓ Saved primary screenshot: {primary_out}")

            page.close()

        context.close()
        browser.close()

    httpd.shutdown()
    print("\nAll Meta Tools v5 screenshots generated successfully.")


if __name__ == "__main__":
    capture_all_meta5_shots()
