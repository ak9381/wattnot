// Checks for a MOCK efficiency statement, a port of wattnot.certificate.rules.verify_certificate
// and verify_register. Runs in the browser; nothing is sent anywhere.
import { canonicalize, sha256Hex, verifyDocument } from "./wattnot-verify.mjs";

export const FORMAT = "wattnot.certificate/1";
export const REGISTER_FORMAT = "wattnot.cert-register/1";
export const BANNED = ["neutral", "offset", "carbon-free", "carbon free", "net zero", "net-zero"];
export const MARK = ["MOCK", "NOT TRADABLE", "NOT A CARBON CREDIT"];
const enc = new TextEncoder();

export async function canonicalSha256(doc) {
  const body = {};
  for (const k of Object.keys(doc)) if (k !== "integrity") body[k] = doc[k];
  return sha256Hex(enc.encode(canonicalize(body)));
}

export async function verifyRegister(reg) {
  const bad = [];
  if (reg.format !== REGISTER_FORMAT) bad.push(`format is ${JSON.stringify(reg.format)}`);
  let prev = null;
  const seen = {};
  for (const [i, e] of (reg.entries || []).entries()) {
    const tag = e.serial || `#${i + 1}`;
    if ((e.prev_sha256 ?? null) !== prev) bad.push(`${tag}: prev_sha256 does not match the entry before it`);
    if ((e.integrity || {}).sha256 !== (await canonicalSha256(e))) bad.push(`${tag}: entry was edited (hash mismatch)`);
    if (e.serial !== `WNC-MOCK-${String(i + 1).padStart(4, "0")}`) bad.push(`${tag}: serial out of sequence`);
    for (const k of e.claim_keys || []) { if (k in seen) bad.push(`${tag}: claim ${k} was already certified by ${seen[k]}`); seen[k] = tag; }
    prev = (e.integrity || {}).sha256 ?? null;
  }
  return bad;
}

// Returns [{name, ok, detail}] in display order.
export async function checkCertificate(cert, { register = null, receipt = null, bundle = null, trusted = null } = {}) {
  const out = [];
  const add = (name, ok, detail) => out.push({ name, ok, detail });
  add("format", cert.format === FORMAT, cert.format === FORMAT ? FORMAT : `format is ${JSON.stringify(cert.format)}`);
  let got = null;
  try { got = await canonicalSha256(cert); } catch (e) { got = null; }
  const want = (cert.integrity || {}).sha256;
  add("fingerprint", !!got && got === want, got === want ? `${want.slice(0, 24)}… matches` : "the statement was changed after it was minted");
  const m = cert.watermark || {};
  const markOk = cert.status === "MOCK" && m.permanent === true && JSON.stringify(m.lines) === JSON.stringify(MARK)
    && String(cert.serial || "").startsWith("WNC-MOCK-") && String(cert.title || "").includes("(MOCK)");
  add("mock mark", markOk, markOk ? "MOCK · NOT TRADABLE · NOT A CARBON CREDIT is part of the signed text" : "the MOCK mark is missing or changed");
  const rest = {}; for (const k of Object.keys(cert)) if (k !== "watermark") rest[k] = cert[k];
  const hit = BANNED.filter((w) => JSON.stringify(rest).toLowerCase().includes(w));
  add("wording", hit.length === 0, hit.length ? `banned wording: ${hit.join(", ")}` : "no offset, neutral or net-zero wording");
  if (register) {
    const bad = await verifyRegister(register);
    const e = (register.entries || []).find((x) => x.serial === cert.serial);
    const linked = !!e && e.certificate_sha256 === want && e.receipt_sha256 === (cert.receipt || {}).sha256;
    add("register", bad.length === 0 && linked, bad.length ? bad[0] : linked ? `${cert.serial} is in the hash-chained register` : "the register does not list this statement");
  }
  if (receipt) {
    let r = null;
    try { r = await verifyDocument(receipt, bundle, trusted); } catch { r = null; }
    const sha = r && r.steps[1].got;
    const ok = !!r && r.grade !== "UNVERIFIABLE" && sha === cert.receipt.sha256 && r.grade === cert.receipt.grade;
    add("receipt", ok, ok ? `the receipt re-checks here as ${r.grade}` : "the receipt does not match what the statement quotes");
  }
  return out;
}
