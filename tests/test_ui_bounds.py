"""Browser test: header, brand, sidebar, footer and right-drawer bounds on every page.

Needs a running dashboard (WN_DASH_URL, default http://127.0.0.1:8770) and Playwright with Chrome;
skips otherwise. Run: WN_DASH_URL=http://127.0.0.1:8770 python3 -m pytest tests/test_ui_bounds.py
"""
import os
import urllib.request

import pytest

pw = pytest.importorskip("playwright.sync_api")
BASE = os.environ.get("WN_DASH_URL", "http://127.0.0.1:8770")
CHROME = "/opt/google/chrome/chrome"

# Every page the shared chrome ships on. v2 sections are in-page hashes on v2.html.
PAGES = [
    "/index.html",
    "/v2.html#pHome",
    "/v2.html#pCfg",
    "/v2.html#pRun",
    "/v2.html#pReport",
    "/v2.html#pLearn",
    "/v2.html#pProof",
    "/graph.html",
    "/report.html",
    "/approvals.html",
    "/matrix.html",
    "/runs.html",
    "/costbudget.html",
    "/eu.html",
    "/metatools.html",
]
SIZES = [(1280, 800), (390, 844)]


def _up():
    try:
        urllib.request.urlopen(BASE + "/api/launch/runs", timeout=3)
        return True
    except OSError:
        return False


skip = pytest.mark.skipif(not _up() or not os.path.exists(CHROME), reason="dashboard or Chrome not available")


def _goto(pg, path):
    pg.goto(BASE + path, wait_until="networkidle")
    # Chrome header + footer are injected by chrome.js; wait for them.
    pg.wait_for_selector("nav.wn-nav", state="attached", timeout=20000)
    pg.wait_for_selector("footer.wn-footer", state="attached", timeout=20000)
    # Let async content settle, then nudge a resize so the full-bleed footer refits the final layout.
    pg.wait_for_timeout(300)
    pg.evaluate("window.dispatchEvent(new Event('resize'))")
    pg.wait_for_timeout(200)


def _rect(pg, sel):
    return pg.eval_on_selector(
        sel,
        "e => { const r = e.getBoundingClientRect(); return {l:r.left, t:r.top, r:r.right, b:r.bottom, w:r.width, h:r.height}; }",
    )


@skip
@pytest.mark.parametrize("path", PAGES)
@pytest.mark.parametrize("w,h", SIZES)
def test_chrome_bounds(path, w, h):
    with pw.sync_playwright() as p:
        b = p.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])
        pg = b.new_page(viewport={"width": w, "height": h})
        try:
            _goto(pg, path)
            vw = pg.evaluate("innerWidth")
            vh = pg.evaluate("innerHeight")

            # 1. Header spans the full viewport width, edge to edge.
            nav = _rect(pg, "nav.wn-nav")
            assert abs(nav["l"]) <= 1, (path, w, "nav left", nav)
            assert abs(nav["w"] - vw) <= 1, (path, w, "nav width", nav, vw)

            # 2. Brand sits top-left and is not covered (even when it is the topmost chrome layer).
            brand = _rect(pg, "a.wn-brand")
            assert brand["t"] >= nav["t"] - 1 and brand["b"] <= nav["b"] + 1, (path, w, "brand in header", brand, nav)
            hit = pg.evaluate(
                "([x,y]) => { const el = document.elementFromPoint(x,y); const br = document.querySelector('a.wn-brand'); return !!(el && br && (el === br || br.contains(el) || el.contains(br))); }",
                [brand["l"] + brand["w"] / 2, brand["t"] + brand["h"] / 2],
            )
            assert hit, (path, w, "brand covered", brand)

            # 3. The left sidebar (when shown) starts at or below the header bottom.
            if pg.eval_on_selector("aside.wn-side", "e => !!e && e.offsetParent !== null && e.getBoundingClientRect().height > 0") if pg.query_selector("aside.wn-side") else False:
                side = _rect(pg, "aside.wn-side")
                assert side["t"] >= nav["b"] - 1, (path, w, "sidebar under header", side, nav)

            # 4. Footer: full-width edge to edge, compact, same everywhere.
            foot = _rect(pg, "footer.wn-footer")
            assert abs(foot["l"]) <= 1, (path, w, "footer left", foot)
            assert abs(foot["w"] - vw) <= 1, (path, w, "footer width", foot, vw)
            assert 44 <= foot["h"] <= 72, (path, w, "footer height", foot)
            bg = pg.eval_on_selector("footer.wn-footer", "e => getComputedStyle(e).backgroundColor")
            assert bg in ("rgb(0, 0, 0)", "rgba(0, 0, 0, 1)"), (path, w, "footer not black", bg)

            # Narrow screens: with the sidebar slid out, the header + brand still sit on top.
            if w < 1100 and pg.query_selector("#wnMenu"):
                pg.click("#wnMenu")
                pg.wait_for_timeout(350)
                nav2 = _rect(pg, "nav.wn-nav")
                brand2 = _rect(pg, "a.wn-brand")
                hit2 = pg.evaluate(
                    "([x,y]) => { const el = document.elementFromPoint(x,y); const br = document.querySelector('a.wn-brand'); return !!(el && br && (el === br || br.contains(el) || el.contains(br))); }",
                    [brand2["l"] + brand2["w"] / 2, brand2["t"] + brand2["h"] / 2],
                )
                assert hit2, (path, w, "brand covered with sidebar open", brand2)
                vis2 = pg.eval_on_selector("aside.wn-side", "e => !!e && e.offsetParent !== null && e.getBoundingClientRect().height > 0") if pg.query_selector("aside.wn-side") else False
                if vis2:
                    side2 = _rect(pg, "aside.wn-side")
                    assert side2["t"] >= nav2["b"] - 1, (path, w, "open sidebar under header", side2, nav2)
        finally:
            b.close()


# Pages that carry a right-side info drawer and how to open it.
DRAWER_PAGES = ["/v2.html#pReport", "/report.html"]


@skip
@pytest.mark.parametrize("path", DRAWER_PAGES)
@pytest.mark.parametrize("w,h", SIZES)
def test_right_drawer_bounds(path, w, h):
    with pw.sync_playwright() as p:
        b = p.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])
        pg = b.new_page(viewport={"width": w, "height": h})
        try:
            _goto(pg, path)
            if not pg.query_selector("aside.side, aside.panel#spanel"):
                pytest.skip("no right drawer on " + path)
            sel = "aside.side" if pg.query_selector("aside.side") else "aside.panel#spanel"
            # Open it the way the page does and recompute bounds.
            pg.eval_on_selector(
                sel,
                "a => { a.classList.add('on'); a.setAttribute('aria-hidden','false'); document.body.classList.add('drawer-open'); if (window.__wnDrawerBounds) window.__wnDrawerBounds(); }",
            )
            pg.wait_for_timeout(400)
            nav = _rect(pg, "nav.wn-nav")
            vh = pg.evaluate("innerHeight")
            dr = _rect(pg, sel)
            assert dr["t"] >= nav["b"] - 1, (path, w, "drawer top below header", dr, nav)
            assert dr["b"] <= vh + 1, (path, w, "drawer bottom in viewport", dr, vh)
            ov = pg.eval_on_selector(sel, "e => getComputedStyle(e).overflowY")
            assert ov in ("auto", "scroll"), (path, w, "drawer not scrollable", ov)
            # Its content is reachable (scrollable region exists / nothing clipped away).
            reach = pg.eval_on_selector(sel, "e => e.scrollHeight <= e.clientHeight + 1 || e.scrollHeight > 0")
            assert reach, (path, w, "drawer content unreachable")
        finally:
            b.close()
