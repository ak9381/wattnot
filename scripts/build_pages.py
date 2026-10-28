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

from starlette.testclient import TestClient  # noqa: E402

from wattnot.dash.server import create_app  # noqa: E402

SHIM = r"""// Static replay: serve recorded API answers from ./api/*.json.
(() => {
  const BASE = new URL(".", document.currentScript.src).pathname.replace(/dash\/$/, "");
  const key = (o) => JSON.stringify(Object.keys(o).sort().reduce((a, k) => ((a[k] = o[k]), a), {}));
  let index = null;
  const real = window.fetch.bind(window);
  window.fetch = async (u, init = {}) => {
    let p = typeof u === "string" ? u : u.url;
    if (p.startsWith("/api/")) {
      if ((init.method || "GET").toUpperCase() === "POST" && p === "/api/dash/v2/run") {
        const body = JSON.parse(init.body || "{}"); delete body.compare;
        index ??= await real(`${BASE}api/dash/v2/run/index.json`).then((r) => r.json()).catch(() => ({}));
        const f = index[key(body)];
        if (f) return real(`${BASE}api/dash/v2/run/${f}`);
        return new Response(JSON.stringify({ detail: "This combination of settings was not recorded for the static replay. Change one setting at a time from the default, or run the dashboard locally." }), { status: 404, headers: { "content-type": "application/json" } });
      }
      return real(`${BASE}${p.slice(1).split("?")[0]}.json`);
    }
    if (p.startsWith("/room/")) return real(`${BASE}${p.slice(1)}`);
    return real(u, init);
  };
  document.addEventListener("DOMContentLoaded", () => {
    const b = document.createElement("div"); b.className = "replay-banner"; b.setAttribute("role", "note");
    b.textContent = "Replay of measured logs. This static page plays back recorded RUN2 data; no GPU or model is running and nothing you click changes a real system.";
    document.body.prepend(b);
  });
})();
"""


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
              "/api/dash/v2/runs", "/api/dash/v2/verify", "/api/dash/v2/judge-path"]:
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
    for css in (out / "dash").glob("*.css"):
        css.write_text(css.read_text().replace("/run/vendor/", "../run/vendor/"))
    v2 = out / "dash/v2.html"
    s = v2.read_text().replace('<link rel="stylesheet" href="v2.css">',
                               '<script src="static-api.js"></script>\n<link rel="stylesheet" href="v2.css">', 1)
    s = s.replace('<a class="btn" href="/">Classic view</a>', '<a class="btn" href="https://github.com/ak9381/wattnot">Source</a>', 1)
    v2.write_text(s)
    (out / "index.html").write_text('<!doctype html><meta charset="utf-8"><title>WattNot replay</title>'
                                    '<meta http-equiv="refresh" content="0; url=dash/v2.html">'
                                    '<a href="dash/v2.html">WattNot: replay of measured logs</a>\n')
    (out / ".nojekyll").write_text("")
    print(f"site built: {n} run settings recorded")


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "site")
