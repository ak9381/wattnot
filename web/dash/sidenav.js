// Sidebar sections for the current page, and one section at a time so no page needs long scrolling.
// A page is cut into blocks of at most ~0.9 screen; each block gets a sidebar entry named after its
// first heading. Tiny strips (legends, the configuration bar) stay visible above every block.
const SKIP = /v2\.html#p(?:Cfg|Run|Report)$|graph\.html|index\.html|\/dash\/$/;
const vis = (e) => e.offsetParent !== null && !e.matches("script, style, nav, aside, footer, .wn-side-scrim");
const clean = (t) => (t || "").replace(/\s+/g, " ").trim().slice(0, 44);

function rootOf() {
  let r = location.hash && location.pathname.endsWith("v2.html") ? document.querySelector(location.hash) : (document.querySelector("main") || document.body);
  if (!r) return null;
  for (let i = 0; i < 4; i++) {
    const kids = [...r.children].filter(vis), big = kids.filter((k) => k.offsetHeight > 60);
    if (big.length === 1 && big[0].offsetHeight > innerHeight * 1.2 && big[0].children.length > 1) r = big[0]; else break;
  }
  return r;
}

function blocks(root, max) {
  const out = [];
  const walk = (parent, depth) => {
    let cur = null;
    for (const k of [...parent.children].filter(vis)) {
      if (k.offsetHeight < 44) continue;                     // strips stay visible
      if (k.offsetHeight > max * 1.3 && depth < 3 && [...k.children].filter(vis).length > 1) { cur = null; walk(k, depth + 1); continue; }
      if (!cur || cur.h + k.offsetHeight > max) { cur = { els: [], h: 0 }; out.push(cur); }
      cur.els.push(k); cur.h += k.offsetHeight;
    }
  };
  walk(root, 0);
  return out.map((b, i) => {
    const hd = b.els.map((e) => (e.matches("h1,h2,h3,h4") ? e : e.querySelector("h1,h2,h3,h4"))).find(Boolean);
    return { ...b, id: "part-" + (i + 1), title: clean(hd?.textContent) || `Part ${i + 1}` };
  });
}

let built = "";
function build() {
  const a = document.querySelector("#wnSide a.wn-link.on"); if (!a) return;
  const host = a.parentElement.querySelector(".wn-secs"); if (!host) return;
  if (SKIP.test(location.pathname + location.hash)) { host.innerHTML = ""; return; }
  const all = new URL(location.href).searchParams.get("all") === "1";
  document.querySelectorAll(".wn-sec-hidden").forEach((e) => e.classList.remove("wn-sec-hidden"));
  const root = rootOf(); if (!root) return;
  const parts = blocks(root, innerHeight * 0.9);
  const sig = location.pathname + location.hash + parts.map((p) => p.els.length).join(",");
  if (parts.length < 2) { host.innerHTML = ""; return; }
  const want = (location.hash.match(/^#part-(\d+)$/) || [])[1];
  let cur = want ? +want - 1 : Math.min(+(host.dataset.cur || 0), parts.length - 1);
  host.innerHTML = parts.map((p, i) => `<li><a href="#${p.id}" data-sec="${i}">${p.title.replace(/[&<>]/g, "")}</a></li>`).join("")
    + `<li><a href="#" data-allsec="1">${all ? "One part at a time" : "Show the whole page"}</a></li>`;
  const show = (i) => {
    host.dataset.cur = i;
    host.querySelectorAll("[data-sec]").forEach((l) => l.classList.toggle("on", +l.dataset.sec === i));
    if (!all) parts.forEach((p, j) => p.els.forEach((e) => e.classList.toggle("wn-sec-hidden", j !== i)));
  };
  host.querySelectorAll("[data-sec]").forEach((l) => l.addEventListener("click", (e) => {
    e.preventDefault(); show(+l.dataset.sec);
    if (all) parts[+l.dataset.sec].els[0].scrollIntoView({ block: "start" }); else scrollTo({ top: 0 });
  }));
  host.querySelector("[data-allsec]").addEventListener("click", (e) => {
    e.preventDefault(); const u = new URL(location.href);
    if (all) u.searchParams.delete("all"); else u.searchParams.set("all", "1");
    location.href = u.toString();
  });
  show(cur);
  built = sig;
}

function placeRunPicker() {
  const box = document.getElementById("wnRunPickBox"), slot = document.getElementById("wnSideRun");
  if (box && slot && box.parentElement !== slot) slot.appendChild(box);
}

let t = null, n = 0;
const later = (ms = 500) => { clearTimeout(t); t = setTimeout(() => { placeRunPicker(); if (n++ < 6 || !built) build(); }, ms); };
addEventListener("hashchange", () => { n = 0; built = ""; later(200); });
addEventListener("load", () => later(800));
new MutationObserver((m) => { if (m.some((r) => !r.target.closest?.("#wnSide"))) later(); }).observe(document.body, { childList: true, subtree: true });
later();
window.__wnSections = () => build();
