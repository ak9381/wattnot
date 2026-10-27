// Node tests for the in-browser verifier and certificate checks. Run: node --test tests/
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { verifyDocument, flipOneDigit, canonicalize } from "../js/wattnot-verify.mjs";
import { checkCertificate, verifyRegister } from "../js/certcheck.mjs";

const read = (p) => JSON.parse(readFileSync(new URL("../data/" + p, import.meta.url), "utf8"));
const trusted = read("trusted-keys.json").keys.map((k) => k.signer);
const samples = read("receipts/index.json");

for (const s of samples) {
  test(`receipt ${s.id} grades ${s.expected_grade}`, async () => {
    const r = await verifyDocument(read(`receipts/${s.id}.json`), read(`receipts/${s.id}.sigstore.json`), trusted);
    assert.equal(r.grade, s.expected_grade);
  });
}

test("changing one digit breaks every real receipt", async () => {
  for (const s of samples.filter((x) => x.id.startsWith("real-") && x.expected_grade !== "NOISE")) {
    const text = JSON.stringify(read(`receipts/${s.id}.json`), null, 2);
    const flipped = flipOneDigit(text, 0);
    assert.notEqual(flipped, text);
    const r = await verifyDocument(JSON.parse(flipped), read(`receipts/${s.id}.sigstore.json`), trusted);
    assert.equal(r.grade, "UNVERIFIABLE", s.id);
  }
});

test("canonical JSON sorts keys and keeps numbers", () => {
  assert.equal(canonicalize({ b: 1, a: [true, null, "x"], c: 0.5 }), '{"a":[true,null,"x"],"b":1,"c":0.5}');
});

const idx = read("certificate/index.json");
const register = read("certificate/register.json");

for (const m of idx.minted) {
  test(`certificate ${m.serial} checks out, and fails when edited`, async () => {
    const cert = read(`certificate/${m.serial}.json`), receipt = read(`receipts/${m.receipt}.json`), bundle = read(`receipts/${m.receipt}.sigstore.json`);
    const ok = await checkCertificate(cert, { register, receipt, bundle, trusted });
    assert.ok(ok.every((c) => c.ok), JSON.stringify(ok.filter((c) => !c.ok)));
    const edited = structuredClone(cert); edited.figures.saving_pct.mean += 1;
    assert.equal((await checkCertificate(edited, { register, receipt, bundle, trusted })).find((c) => c.name === "fingerprint").ok, false);
    const bare = structuredClone(cert); bare.status = "OK"; bare.watermark = { lines: [], permanent: false };
    assert.equal((await checkCertificate(bare, { register })).find((c) => c.name === "mock mark").ok, false);
  });
}

test("the register chain holds, and breaks when an entry is edited", async () => {
  assert.deepEqual(await verifyRegister(register), []);
  const bad = structuredClone(register); bad.entries[0].grade = "VALID";
  assert.ok((await verifyRegister(bad)).length > 0);
});
