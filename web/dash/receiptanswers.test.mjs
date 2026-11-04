import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { answerFromReceipts } from "./receiptanswers.js";
const R = (s) => JSON.parse(readFileSync(new URL(`../../receipts/run2/rcpt_20261027_run2_cap350${s}.json`, import.meta.url)));
const RR = JSON.parse(readFileSync(new URL("../../receipts/rerun-1031/rcpt_20261031_rerun_cap325.json", import.meta.url)));
const docs = { receipt: RR, wonder: R(".wonder_live"), musecode: R(".musecode_pr"), sam: R(".sam_hotspot") };
test("old RUN2 cap trials are no longer answered", () => {
  assert.equal(answerFromReceipts("Why was 325 watts rejected?", docs), null);
});
test("headline and quality answers use receipt numbers", () => {
  assert.match(answerFromReceipts("how much energy was saved", docs).text, /34\.9%.*177\.67 → 115\.71/);
  assert.match(answerFromReceipts("what is the quality metric", docs).text, /94\.1%.*1\.0 pt/);
  assert.match(answerFromReceipts("show the PR", docs).text, /wattnot-demo-deploy\/pull\/1/);
});
test("unknown question falls through", () => assert.equal(answerFromReceipts("hello there", docs), null));
