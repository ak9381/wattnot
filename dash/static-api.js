// Static replay: serve recorded API answers from ./api/*.json.
window.__WN_STATIC = true;
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
