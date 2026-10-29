// Icon-only control buttons on the live-run page carry no text, so each must expose an
// accessible name via aria-label (title alone is an unreliable last resort for screen readers).
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const html = readFileSync(new URL("../../web/run/index.html", import.meta.url), "utf8");

// Every empty-content control button on the page, by id.
const ICON_BUTTONS = ["zin", "zout", "fit", "dclose", "back", "play", "fwd"];

for (const id of ICON_BUTTONS) {
  test(`#${id} button has an aria-label`, () => {
    const m = html.match(new RegExp(`<button id="${id}"[^>]*>`));
    assert.ok(m, `#${id} button not found`);
    assert.match(m[0], /aria-label="[^"]+"/, `#${id} needs a non-empty aria-label`);
  });
}
