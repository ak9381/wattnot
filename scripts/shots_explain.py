#!/usr/bin/env python3
"""shots_explain.py — Playwright screenshot generator for Voice Guide "explain" intent.

Serves web/ locally, injects voiceguide.js + explainmap.js into each page,
submits plain-language explain queries, and saves screenshots of the
lavender glow highlights and glass popovers to:
  /workspace/hackathon/meta5-shots/voice-explain-<page>.png
and:
  /workspace/hackathon/meta5-shots/voice-explain.png
"""

from __future__ import annotations

import functools
import http.server
import json
import os
import socketserver
import threading
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

REPO_ROOT = Path(__file__).resolve().parents[1]
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


def setup_mock_routes(page, root_dir: Path) -> None:
    """Intercept API calls and provide deterministic responses."""

    def handle_runs(route):
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({
                "runs": [
                  {
                    "id": "run2-llama-350w",
                    "name": "run2-llama-350w",
                    "model": "meta-llama/Llama-3.1-8B-Instruct",
                    "date": "2026-10-27",
                  }
                ]
            }),
        )

    def handle_run_detail(route):
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({
                "id": "run2-llama-350w",
                "run_name": "run2-llama-350w",
                "model": "meta-llama/Llama-3.1-8B-Instruct",
                "date": "2026-10-27",
                "phases": {
                    "baseline": {
                        "metrics": {
                            "j_per_req": 15.96,
                            "quality_pct": 93.7,
                            "requests": 500,
                        }
                    },
                    "wattnot": {
                        "cap_w": 350,
                        "metrics": {
                            "j_per_req": 11.63,
                            "quality_pct": 93.7,
                            "p99_change_pct": 7.7,
                            "saved_pct": -27.1,
                            "requests": 500,
                            "label": "MEASURED",
                        },
                    },
                },
            }),
        )

    def handle_story(route):
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({
                "usd_per_kwh": 0.1387,
                "water_l_per_kwh": 0.36,
                "levers": {
                    "cap": {"saved_j_per_req": 4.33, "label": "MEASURED"},
                    "topk": {"saved_j_per_req": 1.25, "label": "ESTIMATE"},
                },
            }),
        )

    def handle_voiceguide(route):
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({
                "intent": "explain",
                "action": "explain",
                "label": "MOCK",
                "text": "WattNot reduced electrical energy consumption by 25% [MOCK] while maintaining response accuracy.",
            }),
        )

    page.route("**/api/dash/v2/runs", handle_runs)
    page.route("**/api/dash/v2/runs/*", handle_run_detail)
    page.route("**/api/dash/story", handle_story)
    page.route("**/api/voiceguide*", handle_voiceguide)


def capture_explain_shots():
    httpd, port = start_local_server(WEB_DIR)
    print(f"Local web server listening on http://127.0.0.1:{port}")

    chrome_path = "/usr/bin/google-chrome" if os.path.exists("/usr/bin/google-chrome") else None

    pages_to_test = [
        {
            "name": "metatools",
            "url": f"http://127.0.0.1:{port}/dash/metatools.html",
            "query": "what does 25% mean?",
            "out": SHOTS_DIR / "voice-explain-metatools.png",
        },
        {
            "name": "runs",
            "url": f"http://127.0.0.1:{port}/dash/runs.html",
            "query": "what does Energy saved mean?",
            "out": SHOTS_DIR / "voice-explain-runs.png",
        },
        {
            "name": "report",
            "url": f"http://127.0.0.1:{port}/dash/report.html?run=run2-llama-350w",
            "query": "what does 350 W mean?",
            "out": SHOTS_DIR / "voice-explain-report.png",
        },
        {
            "name": "v2",
            "url": f"http://127.0.0.1:{port}/dash/v2.html",
            "query": "what does Power mean?",
            "out": SHOTS_DIR / "voice-explain-v2.png",
        },
        {
            "name": "run",
            "url": f"http://127.0.0.1:{port}/run/index.html",
            "query": "what does 350 W mean?",
            "out": SHOTS_DIR / "voice-explain-run.png",
        },
    ]

    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=chrome_path, headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 900})

        for item in pages_to_test:
            page = context.new_page()
            setup_mock_routes(page, REPO_ROOT)

            print(f"Loading {item['url']}...")
            try:
                page.goto(item["url"], wait_until="networkidle", timeout=12000)
            except Exception as e:
                print(f"  Note: {e}, continuing...")

            time.sleep(0.5)

            # Injected voiceguide and explainmap
            vg_css = WEB_DIR / "dash" / "voiceguide.css"
            vg_js = WEB_DIR / "dash" / "voiceguide.js"
            exp_js = WEB_DIR / "dash" / "explainmap.js"

            if vg_css.exists():
                page.add_style_tag(path=str(vg_css))
            if vg_js.exists():
                page.add_script_tag(path=str(vg_js))
            if exp_js.exists():
                page.add_script_tag(path=str(exp_js))

            time.sleep(0.5)

            # Run tagging explicitly
            page.evaluate("if (window.WattNotExplain) window.WattNotExplain.tagElementsWithExplain(document);")

            # Type query into voice guide input if present, or invoke explain handler directly
            input_el = page.query_selector("#vg-text-input")
            if input_el:
                input_el.fill(item["query"])
                submit_btn = page.query_selector("#vg-submit-btn")
                if submit_btn:
                    submit_btn.click()
                else:
                    input_el.press("Enter")
            else:
                page.evaluate(f"if (window.WattNotExplain) window.WattNotExplain.handleExplainQuery('{item['query']}', document);")

            # Wait for explain popover to render
            time.sleep(0.8)

            page.screenshot(path=str(item["out"]), full_page=False)
            print(f"Saved {item['out']}")

            if item["name"] == "metatools":
                # Save primary screenshot voice-explain.png
                primary_out = SHOTS_DIR / "voice-explain.png"
                page.screenshot(path=str(primary_out), full_page=False)
                print(f"Saved primary screenshot: {primary_out}")

            page.close()

        context.close()
        browser.close()

    httpd.shutdown()
    print("All screenshots generated successfully.")


if __name__ == "__main__":
    capture_explain_shots()
