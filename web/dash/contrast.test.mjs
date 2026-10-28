// WCAG contrast check for the Dusk palette (violet.css).
// Every text and status colour must clear AA (4.5:1) against the background
// shades and card surfaces (#2E2B44 and #171724) it is actually drawn on.
// If this fails, lighten the colour in violet.css — do not lower the threshold.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const css = readFileSync(join(here, "violet.css"), "utf8");

// Pull the --v-* hex variables straight from the stylesheet.
const vars = {};
for (const m of css.matchAll(/(--v-[\w-]+)\s*:\s*(#[0-9a-fA-F]{6})\b/g)) vars[m[1]] = m[2];

const srgb = (h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16) / 255);
const lin = (c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4);
const lum = (h) => { const [r, g, b] = srgb(h).map(lin); return 0.2126 * r + 0.7152 * g + 0.0722 * b; };
const ratio = (fg, bg) => { const a = lum(fg) + 0.05, b = lum(bg) + 0.05; return a > b ? a / b : b / a; };

// backgrounds and card surfaces text is placed on
const BG = {
  bg: vars["--v-bg"],
  bg2: vars["--v-bg2"],
  bg3: vars["--v-bg3"],
  violet: vars["--v-violet"],
  s2e: vars["--v-surf-2e"] || "#2E2B44",
  s17: vars["--v-surf-17"] || "#171724",
};

// colour -> which backgrounds and card surfaces it must pass on
const CASES = [
  ["--v-cream", ["bg", "bg2", "bg3", "violet", "s2e", "s17"]],
  ["--v-peach", ["bg", "bg2", "bg3", "violet", "s2e", "s17"]],
  ["--v-sub", ["bg", "bg2", "bg3", "s2e", "s17"]],
  ["--v-mut", ["bg", "bg2", "bg3", "s2e", "s17"]],
  // status + label number colours sit on the dark card surfaces
  ["--v-pass", ["bg", "bg2", "s2e", "s17"]],
  ["--v-reject", ["bg", "bg2", "s2e", "s17"]],
  ["--v-l-measured", ["bg", "bg2", "s2e", "s17"]],
  ["--v-l-estimate", ["bg", "bg2", "s2e", "s17"]],
  ["--v-l-simulated", ["bg", "bg2", "s2e", "s17"]],
  ["--v-l-mock", ["bg", "bg2", "s2e", "s17"]],
  ["--v-l-replay", ["bg", "bg2", "s2e", "s17"]],
  ["--v-l-advice", ["bg", "bg2", "s2e", "s17"]],
];

for (const [name, bgs] of CASES) {
  test(`${name} >= 4.5:1 on ${bgs.join(", ")}`, () => {
    const fg = vars[name];
    assert.ok(fg, `${name} missing from violet.css`);
    for (const key of bgs) {
      const r = ratio(fg, BG[key]);
      assert.ok(r >= 4.5, `${name} (${fg}) on ${key} (${BG[key]}) = ${r.toFixed(2)}:1, need >= 4.5`);
    }
  });
}
