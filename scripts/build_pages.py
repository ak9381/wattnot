"""Build the static GitHub Pages replay into ./site.

Every API answer the dashboard needs is recorded from the real server code
into JSON files; a small fetch shim (static-api.js) serves them. Nothing is
live: the page says so in a banner. Run settings: the default and every
single change from it are recorded; other combinations need the real server.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from starlette.testclient import TestClient  # noqa: E402

from wattnot.dash.server import create_app  # noqa: E402

# The fetch shim lives in web/dash/static-api.js so it is a single source of
# truth and can be unit-tested; copytree already copies it into the site, and
# it is re-written below for clarity.
SHIM = (ROOT / "web" / "dash" / "static-api.js").read_text(encoding="utf-8")


def main(out: Path) -> None:
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(ROOT / "web", out)
    c = TestClient(create_app(replay_only=True))
    api = out / "api"

    def save(path: str, data) -> None:
        f = api / (path.removeprefix("/api/") + ".json")
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(data))

    for p in ["/api/dash/partners", "/api/dash/story", "/api/dash/v2/options", "/api/dash/v2/run2",
              "/api/dash/v2/runs", "/api/dash/v2/verify", "/api/dash/v2/judge-path",
              "/api/dash/v2/frameworks"]:
        r = c.get(p)
        if r.status_code == 200:
            save(p, r.json())
        else:
            print("skip", p, r.status_code)
    for run in c.get("/api/dash/v2/runs").json().get("runs", []):
        save(f"/api/dash/v2/runs/{run['id']}", c.get(f"/api/dash/v2/runs/{run['id']}").json())
    opts = c.get("/api/dash/v2/options").json()["options"]
    base = {o["id"]: o["default"] for o in opts}
    combos = [dict(base)]
    for o in opts:
        for ch in o.get("choices", []):
            if ch["v"] != o["default"] and not (o["id"] == "mode" and ch["v"] == "live"):
                combos.append({**base, o["id"]: ch["v"]})
    n = 0
    index = {}
    for b in combos:
        r = c.post("/api/dash/v2/run", json=b)
        if r.status_code == 200:
            d = api / "dash/v2/run"
            d.mkdir(parents=True, exist_ok=True)
            # the key matches JSON.stringify of the sorted settings
            index[json.dumps({k: b[k] for k in sorted(b)}, separators=(",", ":"), ensure_ascii=False)] = f"{n}.json"
            (d / f"{n}.json").write_text(json.dumps(r.json()))
            n += 1
    (api / "dash/v2/run/index.json").write_text(json.dumps(index))
    (out / "dash/static-api.js").write_text(SHIM)
    # One-click EU report: regenerate from the RUN2 receipt so it never goes stale.
    import build_eu_report  # noqa: E402
    build_eu_report.main(out / "dash/eu")
    for css in (out / "dash").glob("*.css"):
        css.write_text(css.read_text().replace("url(/run/vendor/", "url(../run/vendor/"))
    v2 = out / "dash/v2.html"
    s = v2.read_text().replace('<link rel="stylesheet" href="v2.css">',
                               '<script src="static-api.js"></script>\n<link rel="stylesheet" href="v2.css">', 1)
    s = s.replace('<a class="btn" href="graph.html">Arch graph</a>', '<a class="btn" href="graph.html">Arch graph</a>\n<a class="btn" href="https://github.com/ak9381/wattnot">Source</a>', 1)
    v2.write_text(s)
    # The all-runs dashboard and the per-run report need the same static-api
    # shim; each carries a <!-- static-api --> marker where it is injected.
    for name in ("runs.html", "report.html", "index.html"):
        p = out / "dash" / name
        if p.exists():
            p.write_text(p.read_text().replace("<!-- static-api -->", '<script src="static-api.js"></script>', 1))
    # The classic arch view (dash/graph.html) also replays recorded API answers,
    # so its "Report" node and architecture graph work on the static site.
    ci = out / "dash/graph.html"
    if ci.exists():
        ci.write_text(ci.read_text().replace('<link rel="stylesheet" href="dash.css">',
                                              '<script src="static-api.js"></script>\n<link rel="stylesheet" href="dash.css">', 1))
    (out / "index.html").write_text('<!doctype html><meta charset="utf-8">\n'
                                    '<title>WattNot: cut GPU power per AI inference without losing quality</title>\n'
                                    '<meta name="description" content="Cut GPU power per AI inference without losing quality. Every watt checked, plus every dollar and litre.">\n'
                                    '<meta property="og:title" content="WattNot: cut GPU power per AI inference without losing quality">\n'
                                    '<meta property="og:description" content="Cut GPU power per AI inference without losing quality. Every watt checked, plus every dollar and litre.">\n'
                                    '<meta property="og:image" content="https://ak9381.github.io/wattnot/dash/img/hero-1.png">\n'
                                    '<meta property="og:type" content="website">\n'
                                    '<meta property="og:url" content="https://ak9381.github.io/wattnot/">\n'
                                    '<meta http-equiv="refresh" content="0; url=dash/">\n'
                                    '<a href="dash/">WattNot</a>\n')
    (out / ".nojekyll").write_text("")
    rc = out / "receipts"
    shutil.copytree(ROOT / "verifier/samples/chain", rc)
    shutil.copy(ROOT / "verifier/wattnot_verify.py", rc / "wattnot_verify.py")
    shutil.copytree(ROOT / "receipts/run2", rc / "run2")
    # The LangChain drop-in demo proxy-receipt, so the receipt view can open it on
    # Pages and show the framework badge (MOCK upstream, never a live call).
    shutil.copytree(ROOT / "receipts/frameworks", rc / "frameworks")
    (rc / "keys").mkdir()
    shutil.copy(ROOT / "verifier/trusted-keys.json", rc / "keys/trusted-keys.json")
    names = sorted(p.name for p in rc.iterdir())
    (rc / "index.html").write_text("<!doctype html><meta charset=utf-8><title>WattNot signed receipts</title>"
        "<h1>Signed receipts (replay of measured logs)</h1><p>Signed offline with the repo's public test key, so each grade is capped at ESTIMATE: the check proves the numbers were not edited after signing, not who signed them.</p><p>Download the files, then run "
        "<code>python wattnot_verify.py --chain --trust keys/trusted-keys.json .</code></p><ul>"
        + "".join(f'<li><a href="{n}">{n}</a></li>' for n in names if n not in ("keys", "run2", "frameworks")) + '<li><a href="run2/rcpt_20261027_run2_cap350.json">run2/rcpt_20261027_run2_cap350.json</a> (RUN2, 350 W; check with <code>python wattnot_verify.py --trust keys/trusted-keys.json run2/</code>)</li><li><a href="run2/rcpt_20261027_run2_cap350.sigstore.json">run2/rcpt_20261027_run2_cap350.sigstore.json</a></li><li><a href="frameworks/langchain-demo.json">frameworks/langchain-demo.json</a> (LangChain drop-in demo; MOCK upstream, not a live model call)</li><li><a href="keys/trusted-keys.json">keys/trusted-keys.json</a></li></ul>\n')
    print(f"site built: {n} run settings recorded")


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "site")
