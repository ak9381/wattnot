"""The live graph's full-screen view: one button, the same live element, X or Esc closes."""
from pathlib import Path

WEB = Path(__file__).resolve().parents[1] / "web" / "dash"


def test_full_screen_button_and_close_paths():
    js = (WEB / "graphbar.js").read_text()
    assert 'id="gFull"' in js and "gFullX" in js
    assert 'e.key === "Escape"' in js and "wn-graph-full" in js
    assert 'pc("pc-fit")' in js and 'pc("pc-goto")' in js   # same live graph, no copy


def test_full_screen_css_fills_the_viewport_with_the_palette():
    css = (WEB / "graph.css").read_text()
    assert "body.story.wn-graph-full #stage #amap" in css and "inset:0" in css
    assert "var(--bg" in css and "var(--cy" in css



def test_marching_ants_are_golden_and_glowing():
    css = (WEB / "graph.css").read_text()
    assert "--wn-ant:#FFC940" in css and ".av-edges .e.flow{ stroke:var(--wn-ant)" in css
    assert "drop-shadow(0 0 8px var(--wn-ant2))" in css


def test_full_screen_camera_tour_and_pin_switch():
    js = (WEB / "graphbar.js").read_text()
    assert "tour(3000)" in js and "25000" in js and 'data-cam' in js
    for k in ("auto", "whole", "follow"):
        assert f'["{k}",' in js
    assert "wn-glide" in (WEB / "graph.css").read_text()
