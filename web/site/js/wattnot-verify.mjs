// wattnot-verify.mjs: check a WattNot receipt in the browser (or in Node 20+).
//
// A line-for-line port of verifier/wattnot_verify.py (spec wattnot.receipt/1, open draft v0.1).
// Nothing leaves the page: no network, no WattNot server. SHA-256 and SHA-512 come from
// WebCrypto; the Ed25519 check is plain BigInt maths (RFC 8032), so it does not depend on the
// browser's Ed25519 support.
//
// The steps and the grade are the same as the Python verifier's, and the test suite runs this
// file against the same 9 conformance vectors.

export const SPEC = "wattnot.receipt/1";
export const OFFLINE_MEDIA_TYPE = "application/vnd.wattnot.offline-signature.v1+json";
export const LABELS = ["MEASURED", "ESTIMATE", "SIMULATED"];
export const GRADES = ["VALID", "ESTIMATE", "SIMULATED", "UNVERIFIABLE", "NOISE"];
const REQUIRED = ["spec", "receipt_id", "created_at", "run_id", "subject", "baseline", "candidate",
  "result", "measurement", "planner_trace", "grade", "integrity"];
const ARM_REQUIRED = ["params", "reps", "wh_per_req", "p99_ms", "quality"];
const TOL = 1e-6;

const subtle = () => globalThis.crypto.subtle;
const enc = new TextEncoder();

// ---- JCS (RFC 8785) -------------------------------------------------------------------------
// JSON.stringify writes strings and numbers the way JCS wants (ES number formatting, short
// escapes, \u00xx for other control characters). Keys are sorted by UTF-16 code unit, which is
// what JS's default sort does.

export function canonicalize(v) {
  if (v === null) return "null";
  if (v === true) return "true";
  if (v === false) return "false";
  if (typeof v === "string") return JSON.stringify(v);
  if (typeof v === "number") {
    if (!Number.isFinite(v)) throw new Error("NaN and Infinity are not allowed in JCS");
    return JSON.stringify(v);
  }
  if (Array.isArray(v)) return "[" + v.map(canonicalize).join(",") + "]";
  if (typeof v === "object") {
    const keys = Object.keys(v).sort();
    return "{" + keys.map((k) => JSON.stringify(k) + ":" + canonicalize(v[k])).join(",") + "}";
  }
  throw new Error("cannot canonicalize " + typeof v);
}

export const hex = (buf) => [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("");
export function fromHex(s) {
  if (typeof s !== "string" || s.length % 2 || /[^0-9a-fA-F]/.test(s)) throw new Error("bad hex");
  const out = new Uint8Array(s.length / 2);
  for (let i = 0; i < out.length; i++) out[i] = parseInt(s.slice(2 * i, 2 * i + 2), 16);
  return out;
}
export async function sha256Hex(bytes) { return hex(await subtle().digest("SHA-256", bytes)); }

export async function receiptSha256(receipt) {
  const body = {};
  for (const k of Object.keys(receipt)) if (k !== "integrity") body[k] = receipt[k];
  return sha256Hex(enc.encode(canonicalize(body)));
}

// ---- Ed25519 verify (RFC 8032, BigInt, verify only) -----------------------------------------

const P = 2n ** 255n - 19n;
const Q = 2n ** 252n + 27742317777372353535851937790883648493n;
const mod = (a, m = P) => ((a % m) + m) % m;
function pow(b, e, m = P) {
  let r = 1n; b = mod(b, m);
  while (e > 0n) { if (e & 1n) r = (r * b) % m; b = (b * b) % m; e >>= 1n; }
  return r;
}
const D = mod(-121665n * pow(121666n, P - 2n));
const I = pow(2n, (P - 1n) / 4n);

function recoverX(y, sign) {
  if (y >= P) return null;
  const x2 = mod((y * y - 1n) * pow(D * y * y + 1n, P - 2n));
  if (x2 === 0n) return sign ? null : 0n;
  let x = pow(x2, (P + 3n) / 8n);
  if (mod(x * x - x2) !== 0n) x = mod(x * I);
  if (mod(x * x - x2) !== 0n) return null;
  if ((x & 1n) !== BigInt(sign)) x = P - x;
  return x;
}
const GY = mod(4n * pow(5n, P - 2n));
const GX = recoverX(GY, 0);
const G = [GX, GY, 1n, mod(GX * GY)];

function add(a, b) {
  const A = mod((a[1] - a[0]) * (b[1] - b[0]));
  const B = mod((a[1] + a[0]) * (b[1] + b[0]));
  const C = mod(2n * a[3] * b[3] * D);
  const Dd = mod(2n * a[2] * b[2]);
  const E = B - A, F = Dd - C, Gg = Dd + C, H = B + A;
  return [mod(E * F), mod(Gg * H), mod(F * Gg), mod(E * H)];
}
function mul(s, pt) {
  let r = [0n, 1n, 1n, 0n];
  while (s > 0n) { if (s & 1n) r = add(r, pt); pt = add(pt, pt); s >>= 1n; }
  return r;
}
const eq = (a, b) => mod(a[0] * b[2] - b[0] * a[2]) === 0n && mod(a[1] * b[2] - b[1] * a[2]) === 0n;
function leInt(bytes) { let n = 0n; for (let i = bytes.length - 1; i >= 0; i--) n = (n << 8n) | BigInt(bytes[i]); return n; }
function decode(b) {
  if (b.length !== 32) return null;
  let y = leInt(b);
  const sign = Number(y >> 255n);
  y &= (1n << 255n) - 1n;
  const x = recoverX(y, sign);
  return x === null ? null : [x, y, 1n, mod(x * y)];
}
export async function ed25519Verify(pub, msg, sig) {
  if (pub.length !== 32 || sig.length !== 64) return false;
  const A = decode(pub), R = decode(sig.slice(0, 32));
  const s = leInt(sig.slice(32));
  if (A === null || R === null || s >= Q) return false;
  const cat = new Uint8Array(32 + 32 + msg.length);
  cat.set(sig.slice(0, 32), 0); cat.set(pub, 32); cat.set(msg, 64);
  const h = leInt(new Uint8Array(await subtle().digest("SHA-512", cat))) % Q;
  return eq(mul(s, G), add(R, mul(h, A)));
}

// ---- joules (same rules as wattnot.receipt.joules) ------------------------------------------

const r6 = (x) => (x === null || x === undefined ? null : Math.round(x * 1e6) / 1e6);
const good = (j, pr) => (!pr || pr <= 0 ? null : j / pr);
function armJoules(arm) {
  const wh = arm.wh_per_req;
  const pr = (arm.quality || {}).fixed_pass_rate;
  const j = Number(wh.mean) * 3600;
  const out = { j_per_answer: r6(j), pass_rate: pr === undefined ? null : pr, j_per_good_answer: r6(good(j, pr)) };
  const rng = wh.range;
  if (rng && typeof rng === "object" && !Array.isArray(rng)) {
    const lo = Number(rng.low) * 3600, hi = Number(rng.high) * 3600;
    out.j_per_answer_range = { low: r6(lo), high: r6(hi) };
    out.j_per_good_answer_range = { low: r6(good(lo, pr)), high: r6(good(hi, pr)) };
  }
  return out;
}
const change = (a, b) => (a === null || b === null || a === 0 ? null : Math.round(1e4 * (100 * (b - a) / a)) / 1e4);
export function deriveJoules(receipt) {
  const base = armJoules(receipt.baseline), cand = armJoules(receipt.candidate);
  return {
    unit: "J", label: receipt.measurement.label,
    formula: "j_per_answer = wh_per_req x 3600; j_per_good_answer = j_per_answer / pass_rate",
    baseline: base, candidate: cand,
    change_pct: { j_per_answer: change(base.j_per_answer, cand.j_per_answer),
      j_per_good_answer: change(base.j_per_good_answer, cand.j_per_good_answer) },
  };
}
function same(a, b) {
  const isObj = (x) => x && typeof x === "object" && !Array.isArray(x);
  if (isObj(a) && isObj(b)) {
    const ka = Object.keys(a), kb = Object.keys(b);
    return ka.length === kb.length && ka.every((k) => k in b && same(a[k], b[k]));
  }
  if (typeof a === "number" && typeof b === "number") return Math.abs(a - b) <= TOL;
  if (Array.isArray(a) && Array.isArray(b)) return a.length === b.length && a.every((x, i) => same(x, b[i]));
  return a === b;
}

// ---- steps ----------------------------------------------------------------------------------

const isObj = (x) => x !== null && typeof x === "object" && !Array.isArray(x);
function schemaStep(r) {
  if (!isObj(r) || r.spec !== SPEC) return [false, { error: `spec is ${JSON.stringify(isObj(r) ? r.spec ?? null : null)}, want ${SPEC}` }];
  for (const k of REQUIRED) if (!(k in r)) return [false, { error: `missing field '${k}'` }];
  for (const side of ["baseline", "candidate"]) for (const k of ARM_REQUIRED)
    if (!isObj(r[side]) || !(k in r[side])) return [false, { error: `${side} is missing '${k}'` }];
  const sha = isObj(r.integrity) ? r.integrity.sha256 : null;
  if (typeof sha !== "string" || !/^[0-9a-f]{64}$/.test(sha)) return [false, { error: "integrity.sha256 is not a 64-char hex string" }];
  if (!GRADES.includes(r.grade)) return [false, { error: `unknown grade ${JSON.stringify(r.grade)}` }];
  if (!isObj(r.measurement) || !LABELS.includes(r.measurement.label)) return [false, { error: "measurement.label must be one of " + LABELS.join(", ") }];
  if ("prompt" in r || "prompts" in r || "outputs" in r) return [false, { error: "receipts must not carry prompt or output text" }];
  return [true, { spec: SPEC }];
}

const sigCache = new Map();
async function signatureStep(r, bundle, trusted) {
  if (!isObj(bundle)) return [false, { error: "no bundle given" }];
  if (bundle.mediaType !== OFFLINE_MEDIA_TYPE) return [false, { error: "not an offline bundle" }];
  const got = await receiptSha256(r);
  const integ = r.integrity || {};
  if (got !== bundle.sha256 || got !== integ.sha256) return [false, { error: "sha256 does not match the receipt" }];
  let pub, ok = false;
  try {
    pub = fromHex(bundle.public_key);
    const key = `${bundle.public_key}|${got}|${bundle.signature}`;
    if (!sigCache.has(key)) sigCache.set(key, await ed25519Verify(pub, fromHex(got), fromHex(bundle.signature)));
    ok = sigCache.get(key);
  } catch { ok = false; }
  if (!ok) return [false, { error: "signature does not verify" }];
  const signer = "offline:" + (await sha256Hex(pub)).slice(0, 16);
  if (integ.signer != null && integ.signer !== signer) return [false, { error: `receipt names signer ${integ.signer}, bundle is ${signer}` }];
  if (trusted && !trusted.includes(signer)) return [false, { error: `signer ${signer} is not a trusted key` }];
  return [true, { signer }];
}

function claimedGrade(r, rekor) {
  const res = r.result;
  const w = res.welch || {};
  const tested = w.applicable !== false;
  if (tested && (!res.billable || w.p_value >= (w.alpha ?? 0.05))) return "NOISE";
  const label = r.measurement.label;
  if (label === "SIMULATED") return "SIMULATED";
  if (label !== "MEASURED" || !rekor || !tested) return "ESTIMATE";
  return "VALID";
}

// Same shape as `wattnot verify --json`. `trusted` is an array of signer ids, or null.
export async function verifyDocument(receipt, bundle = null, trusted = null) {
  const steps = [];
  let [ok, d] = schemaStep(receipt);
  steps.push({ name: "schema", ok, ...d });
  const schemaOk = ok;
  const integ = isObj(receipt) && isObj(receipt.integrity) ? receipt.integrity : {};
  const expected = integ.sha256;
  let got = null;
  try {
    got = await receiptSha256(receipt);
    steps.push({ name: "sha256", ok: !!expected && got === expected, expected, got });
  } catch (e) {
    steps.push({ name: "sha256", ok: false, expected, error: String(e.message).slice(0, 200) });
  }
  if (isObj(receipt)) {
    try { [ok, d] = await signatureStep(receipt, bundle, trusted); }
    catch (e) { [ok, d] = [false, { error: String(e.message).slice(0, 200) }]; }
  } else [ok, d] = [false, { error: "receipt is not a JSON object" }];
  steps.push({ name: "signature", ok, ...d });
  const rekor = isObj(bundle) ? bundle.rekor : null;
  const logIndex = isObj(rekor) ? rekor.log_index ?? null : null;
  steps.push({ name: "rekor", ok: logIndex !== null,
    ...(logIndex !== null ? { log_index: logIndex } : { note: "no Rekor entry (offline signature): grade at most ESTIMATE" }) });
  let derived = null, derivedOk = true;
  if (schemaOk) {
    try { derived = deriveJoules(receipt); } catch { derived = null; }
    if ("joules" in receipt) {
      derivedOk = derived !== null && same(receipt.joules, derived);
      steps.push({ name: "joules", ok: derivedOk,
        ...(derivedOk ? { matches: true } : { error: "joules block does not match the receipt's own numbers" }) });
    }
  }
  let g;
  if (!(schemaOk && steps[1].ok && steps[2].ok && derivedOk)) g = "UNVERIFIABLE";
  else { try { g = claimedGrade(receipt, logIndex !== null); } catch { g = "UNVERIFIABLE"; } }
  const claimed = integ.grade ?? null;
  steps.push({ name: "grade", ok: g !== "UNVERIFIABLE", grade: g, claimed });
  return { grade: g, claimed_grade: claimed, steps, joules: derived };
}

// Change one digit inside a number in `text`, for the "edit a digit" demo. Returns the new text.
export function flipOneDigit(text, which = 0) {
  const hits = [...text.matchAll(/"(wh_per_req|mean|p_value|pass_rate|fixed_pass_rate|p99_ms|billed|saving_pct)"\s*:\s*\{?[^0-9\-]*?(-?\d+\.?\d*)/g)];
  const pool = hits.length ? hits : [...text.matchAll(/(\d+\.\d+)/g)].map((m) => ({ 0: m[0], 1: "", 2: m[1], index: m.index }));
  if (!pool.length) return text;
  const m = pool[which % pool.length];
  const num = m[2];
  const at = m.index + m[0].lastIndexOf(num);
  const digits = [...num].map((c, i) => ({ c, i })).filter((x) => /\d/.test(x.c));
  const pick = digits[Math.min(2, digits.length - 1)];
  const repl = String((Number(pick.c) + 1) % 10);
  return text.slice(0, at + pick.i) + repl + text.slice(at + pick.i + 1);
}
