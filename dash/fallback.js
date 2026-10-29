// Live demo safety net: if a dashboard API call times out or the server
// errors, read the same answer from the published replay of measured logs.
(() => {
  if (window.__WN_STATIC) return;
  const PAGES = "https://ak9381.github.io/wattnot/";
  const real = window.fetch.bind(window);
  let told = false;
  const tell = () => {
    // Banner removed per design spec
  };
  // Match static-api.js targetOf: a string is the URL; a Request has .url; a
  // URL object has .href; fall back to String(u) so a non-string arg never throws.
  const targetOf = (u) =>
    typeof u === "string" ? u
      : (u && typeof u.url === "string") ? u.url
      : (u && typeof u.href === "string") ? u.href
      : String(u);
  window.fetch = async (u, init = {}) => {
    const p = targetOf(u);
    const get = (init.method || "GET").toUpperCase() === "GET";
    if (!get || !p.startsWith("/api/dash/")) return real(u, init);
    const ctl = new AbortController(); const t = setTimeout(() => ctl.abort(), 8000);
    try {
      const r = await real(u, { ...init, signal: ctl.signal });
      if (r.status < 500) return r;
    } catch (e) { /* timeout or network error: fall through */ } finally { clearTimeout(t); }
    tell();
    return real(`${PAGES}${p.slice(1).split("?")[0]}.json`);
  };
})();
