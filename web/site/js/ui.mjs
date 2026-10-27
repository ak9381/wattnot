// Small DOM helpers shared by the site pages. Data is always put in with textContent,
// never innerHTML, so a receipt someone pastes in cannot inject markup.

export function el(tag, attrs, ...kids) {
  const n = document.createElementNS(attrs && attrs.ns ? attrs.ns : "http://www.w3.org/1999/xhtml", tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (k === "ns" || v === null || v === undefined || v === false) continue;
    if (k === "class") n.setAttribute("class", v);
    else if (k.startsWith("on") && typeof v === "function") n.addEventListener(k.slice(2), v);
    else n.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) {
    if (kid === null || kid === undefined || kid === false) continue;
    n.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  }
  return n;
}
const SVG = "http://www.w3.org/2000/svg";
export const svg = (tag, attrs, ...kids) => el(tag, { ns: SVG, ...attrs }, ...kids);

export const chip = (label, big = false) => label ? el("span", { class: `chip ${label}${big ? " big" : ""}` }, label) : null;
export function clear(n) { while (n.firstChild) n.removeChild(n.firstChild); return n; }

export async function getJSON(path) {
  const r = await fetch(path);
  if (!r.ok) throw new Error(`${path}: ${r.status}`);
  return r.json();
}

// Fixed-precision numbers without surprises.
export const num = (x, d = 2) => (x === null || x === undefined ? "n/a" : Number(x).toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d }));
export const sig = (x, n = 4) => (x === null || x === undefined ? "n/a" : Number(Number(x).toPrecision(n)).toString());
export const usd = (x, d = 4) => (x === null || x === undefined ? "n/a" : "$" + Number(x).toFixed(d));
export const pct = (x, d = 1) => (x === null || x === undefined ? "n/a" : (x > 0 ? "+" : x < 0 ? "−" : "") + Math.abs(x).toFixed(d) + "%");

// ?still in the URL also turns animation off (used for screenshots and tests)
export function reduced() { return /[?&]still\b/.test(location.search) || (window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches); }
