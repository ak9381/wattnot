import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { answerFromReceipts } from "./receiptanswers.js";
const R = (s) => JSON.parse(readFileSync(new URL(`../../receipts/run2/rcpt_20261027_run2_cap350${s}.json`, import.meta.url)));
const docs = { receipt: R(""), wonder: R(".wonder_live"), musecode: R(".musecode_pr"), sam: R(".sam_hotspot") };
test("live transcript question is answered from the receipt, labelled REPLAY", () => {
  const a = answerFromReceipts("Why was 325 watts rejected?", docs);
  assert.equal(a.label, "REPLAY"); assert.match(a.text, /rejected/); assert.match(a.text, /10\.9%/); assert.doesNotMatch(a.text, /MOCK/);
});
test("headline and quality answers use receipt numbers", () => {
  assert.match(answerFromReceipts("how much energy was saved", docs).text, /27\.1%.*17\.03 → 12\.42/);
  assert.match(answerFromReceipts("what is the quality metric", docs).text, /93\.7%.*1\.0 pt/);
  assert.match(answerFromReceipts("show the PR", docs).text, /wattnot-demo-deploy\/pull\/1/);
});
test("unknown question falls through", () => assert.equal(answerFromReceipts("hello there", docs), null));
