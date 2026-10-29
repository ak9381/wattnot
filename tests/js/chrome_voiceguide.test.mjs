// room/node.html must load the shared chrome (header/footer injected by chrome.js).
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const web = new URL("../../web/", import.meta.url);
const read = (p) => readFileSync(new URL(p, web), "utf8");

test("room/node.html loads the shared chrome (header/footer)", () => {
  const html = read("room/node.html");
  assert.match(html, /<link rel="stylesheet" href="\.\.\/dash\/chrome\.css">/);
  assert.match(html, /<script src="\.\.\/dash\/chrome\.js" defer><\/script>/);
});
