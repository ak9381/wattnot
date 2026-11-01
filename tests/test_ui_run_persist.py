"""Browser test: the header run picker keeps the same run on every page.

Needs a running dashboard (WN_DASH_URL, default http://127.0.0.1:8770) and Playwright with Chrome;
skips otherwise. Run: WN_DASH_URL=http://127.0.0.1:8770 python3 -m pytest tests/test_ui_run_persist.py
"""
import os
import urllib.request

import pytest

pw = pytest.importorskip("playwright.sync_api")
BASE = os.environ.get("WN_DASH_URL", "http://127.0.0.1:8770")
CHROME = "/opt/google/chrome/chrome"


def _up():
    try:
        urllib.request.urlopen(BASE + "/api/launch/runs", timeout=3)
        return True
    except OSError:
        return False


@pytest.mark.skipif(not _up() or not os.path.exists(CHROME), reason="dashboard or Chrome not available")
def test_selected_run_persists_across_every_nav_page():
    with pw.sync_playwright() as p:
        b = p.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])
        pg = b.new_page(viewport={"width": 1440, "height": 900})
        pg.goto(BASE + "/index.html", wait_until="networkidle")
        pg.wait_for_selector("#wnRunPick option[value]", state="attached", timeout=20000)
        opts = pg.eval_on_selector_all("#wnRunPick option", "o => o.map(x => x.value)")
        default = pg.eval_on_selector("#wnRunPick", "s => s.value")
        pick = next(v for v in opts if v != default)
        with pg.expect_navigation():
            pg.select_option("#wnRunPick", pick)
        pg.wait_for_selector("#wnRunPick option[value]", state="attached")
        links = pg.eval_on_selector_all("nav.wn-nav a.wn-link", "a => a.map(x => [x.dataset.nav, x.getAttribute('href')])")
        assert links
        for nav, href in links:
            assert f"run={pick}".replace(":", "%3A") in href or f"run={pick}" in href, (nav, href)
            pg.click(f'nav.wn-nav a.wn-link[data-nav="{nav}"]')
            pg.wait_for_load_state("networkidle")
            assert "run=" in pg.url, (nav, pg.url)
            if nav == "configure":
                assert pg.is_hidden("#wnRunPickBox")
                continue
            try:
                pg.wait_for_function("v => document.querySelector('#wnRunPick')?.value === v", arg=pick, timeout=20000)
            except Exception:
                raise AssertionError((nav, pg.url, pg.evaluate("document.querySelector('#wnRunPick')?.value")))
            box = pg.eval_on_selector("#wnRunPickBox", "e => { const r = e.getBoundingClientRect(); return [r.top, r.bottom] }")
            assert box[0] >= 0, (nav, box)
        pg.reload(wait_until="networkidle")
        pg.wait_for_function("v => document.querySelector('#wnRunPick')?.value === v", arg=pick, timeout=20000)
        # localStorage fallback: a bare URL still shows the picked run
        pg.goto(BASE + "/matrix.html", wait_until="networkidle")
        pg.wait_for_function("v => document.querySelector('#wnRunPick')?.value === v", arg=pick, timeout=20000)
        b.close()
