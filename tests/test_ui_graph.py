"""Browser test for graph.html: one header, a single toolbar below it, the canvas
below the toolbar, and every legend tucked inside the right-side drawer until the
Legend button is pressed.

Needs a running dashboard (WN_DASH_URL, default http://127.0.0.1:8770) and Playwright with Chrome;
skips otherwise. Run: WN_DASH_URL=http://127.0.0.1:8771 python3 -m pytest tests/test_ui_graph.py
"""
import os
import urllib.request

import pytest

pw = pytest.importorskip("playwright.sync_api")
BASE = os.environ.get("WN_DASH_URL", "http://127.0.0.1:8770")
CHROME = "/opt/google/chrome/chrome"
SIZES = [(1280, 800), (390, 844)]


def _up():
    try:
        urllib.request.urlopen(BASE + "/api/launch/runs", timeout=3)
        return True
    except OSError:
        return False


skip = pytest.mark.skipif(not _up() or not os.path.exists(CHROME), reason="dashboard or Chrome not available")


def _goto(pg):
    pg.goto(BASE + "/graph.html", wait_until="networkidle")
    pg.wait_for_selector("nav.wn-nav", state="attached", timeout=20000)
    pg.wait_for_selector("#gbar", state="attached", timeout=20000)
    pg.wait_for_timeout(400)
    pg.evaluate("window.dispatchEvent(new Event('resize'))")
    pg.wait_for_timeout(300)


def _rect(pg, sel):
    return pg.eval_on_selector(
        sel,
        "e => { const r = e.getBoundingClientRect(); return {l:r.left, t:r.top, r:r.right, b:r.bottom, w:r.width, h:r.height}; }",
    )


# Visible = rendered, on-screen and not collapsed/hidden.
_VIS = (
    "e => { if(!e) return false; const s=getComputedStyle(e);"
    " if(s.visibility==='hidden'||s.display==='none'||+s.opacity===0) return false;"
    " const b=e.getBoundingClientRect();"
    " return b.width>0&&b.height>0&&b.bottom>0&&b.top<innerHeight&&b.right>0&&b.left<innerWidth; }"
)


@skip
@pytest.mark.parametrize("w,h", SIZES)
def test_graph_layout(w, h):
    with pw.sync_playwright() as p:
        b = p.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])
        pg = b.new_page(viewport={"width": w, "height": h})
        try:
            _goto(pg)

            # 1. Exactly one visible header: the shared sticky nav. No second bar overlaps it.
            headers = pg.eval_on_selector_all(
                "nav.wn-nav, header, .wn-elig",
                "(els, vis) => els.filter(e => new Function('e', 'return (' + vis + ')(e)')(e)).length",
                _VIS,
            )
            assert headers == 1, (w, "visible headers", headers)

            # 2. The toolbar sits at or below the header bottom.
            nav = _rect(pg, "nav.wn-nav")
            bar = _rect(pg, "#gbar")
            assert bar["t"] >= nav["b"] - 1, (w, "toolbar below header", bar, nav)

            # 3. The canvas starts at or below the toolbar bottom (never under it).
            canvas = _rect(pg, "#amap")
            assert canvas["t"] >= bar["b"] - 1, (w, "canvas below toolbar", canvas, bar)

            # 4. The footer is a full-width bar pinned to the page end, not over the canvas top.
            foot = _rect(pg, "footer.wn-footer")
            assert abs(foot["l"]) <= 1 and abs(foot["w"] - pg.evaluate("innerWidth")) <= 1, (w, "footer full width", foot)
            assert foot["t"] >= canvas["t"] - 1, (w, "footer not over canvas top", foot, canvas)

            # 5. No legend is visible outside the drawer before Legend is pressed.
            out = pg.eval_on_selector_all(
                ".lab-legend, .wn-legend, #afil, .avlegend",
                "(els, vis) => els.filter(e => !e.closest('#legdrawer') && new Function('e', 'return (' + vis + ')(e)')(e)).length",
                _VIS,
            )
            assert out == 0, (w, "legends leaking outside the drawer", out)
            assert not pg.eval_on_selector("#legdrawer", _VIS), (w, "legend drawer open before click")

            # 6. Press Legend: the drawer opens below the header and carries the legend.
            pg.click("#gLegend")
            pg.wait_for_timeout(400)
            assert pg.eval_on_selector("#legdrawer", _VIS), (w, "legend drawer did not open")
            dr = _rect(pg, "#legdrawer")
            assert dr["t"] >= nav["b"] - 1, (w, "drawer top below header", dr, nav)
            assert dr["b"] <= h + 1, (w, "drawer bottom in viewport", dr, h)
            # It contains a real legend (the number-label rows live only in the drawer).
            has_legend = pg.eval_on_selector_all(
                "#legdrawer .leg-row, #legdrawer .lab-legend, #legdrawer #afil",
                "(els, vis) => els.some(e => new Function('e', 'return (' + vis + ')(e)')(e))",
                _VIS,
            )
            assert has_legend, (w, "drawer has no visible legend")
        finally:
            b.close()


@skip
@pytest.mark.parametrize("w,h", SIZES)
def test_graph_badges(w, h):
    """The Meta-models and WattNot-features badges each open the info drawer
    (below the header) and list the selected run's eligibility items."""
    with pw.sync_playwright() as p:
        b = p.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])
        pg = b.new_page(viewport={"width": w, "height": h})
        try:
            _goto(pg)
            nav = _rect(pg, "nav.wn-nav")

            # Both badges exist in the toolbar; the drawer is shut before any click.
            pg.wait_for_selector("#gMeta", state="attached", timeout=20000)
            pg.wait_for_selector("#gFeat", state="attached", timeout=20000)
            assert not pg.eval_on_selector("#infodrawer", _VIS), (w, "info drawer open before click")

            for badge, title in (("#gMeta", "Meta models"), ("#gFeat", "WattNot features")):
                pg.click(badge)
                pg.wait_for_timeout(500)
                assert pg.eval_on_selector("#infodrawer", _VIS), (w, badge, "info drawer did not open")
                dr = _rect(pg, "#infodrawer")
                assert dr["t"] >= nav["b"] - 1, (w, badge, "info drawer top below header", dr, nav)
                assert dr["b"] <= h + 1, (w, badge, "info drawer bottom in viewport", dr, h)
                assert pg.inner_text("#infoTitle").strip() == title, (w, badge, "drawer title")
                # It lists real items (models or features), with the arm toggle present.
                items = pg.eval_on_selector_all(
                    "#infodrawer .info-item",
                    "(els, vis) => els.filter(e => new Function('e','return ('+vis+')(e)')(e)).length",
                    _VIS,
                )
                assert items > 0, (w, badge, "no items listed", items)
                assert pg.eval_on_selector_all("#infodrawer .info-arm [data-arm]", "e => e.length") == 2, (w, badge, "arm toggle missing")

            # Clicking the open badge again closes the drawer.
            pg.click("#gFeat")
            pg.wait_for_timeout(400)
            assert not pg.eval_on_selector("#infodrawer", _VIS), (w, "info drawer did not close on second click")
        finally:
            b.close()
