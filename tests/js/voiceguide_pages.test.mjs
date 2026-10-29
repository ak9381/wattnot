// voiceguide.js must load on these pages, each following its neighbours' relative path.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const web = new URL("../../web/", import.meta.url);
const read = (p) => readFileSync(new URL(p, web), "utf8");

const VOICEGUIDE_PAGES = [
  { page: "dash/costbudget.html", src: "./voiceguide.js" },
  { page: "room/index.html", src: "../dash/voiceguide.js" },
  { page: "room/node.html", src: "../dash/voiceguide.js" },
];

for (const { page, src } of VOICEGUIDE_PAGES) {
  test(`${page} loads voiceguide.js`, () => {
    const html = read(page);
    assert.ok(
      html.includes(`<script type="module" src="${src}"></script>`),
      `${page} must load voiceguide.js from ${src}`
    );
  });
}
