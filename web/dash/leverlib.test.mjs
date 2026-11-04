import assert from "node:assert/strict";
import { armRows, stateText } from "./leverlib.js";
const rows = armRows({ without: { wh: 2, usd: 0.0003, water_l: 0.0007 }, with: { wh: 1.5, usd: 0.0002, water_l: 0.0005 } });
assert.equal(rows[1][3], "-25.0%"); assert.match(rows[3][0], /ESTIMATE/); assert.match(rows[1][0], /MEASURED/);
assert.match(stateText({ phase: "done", lever: "fp8", error: "stopped from the UI", restore: { verified: true } }), /restored and verified/);
assert.match(stateText({ phase: "done", result: { lever: "fp8", kept: false, why: "2 of 16 answers changed" }, restore: { verified: false, problems: ["max-num-seqs is not 64"] } }), /NOT verified/);
console.log("leverlib ok");
