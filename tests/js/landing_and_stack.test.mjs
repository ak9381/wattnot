import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const dashDir = path.resolve(__dirname, "../../web/dash");

test("landing page has head tags, Open Graph tags, and text content", () => {
  const html = fs.readFileSync(path.join(dashDir, "index.html"), "utf8");
  assert.match(html, /<title>WattNot: cut GPU power per AI inference without losing quality<\/title>/);
  assert.match(html, /<meta name="description" content="[^"]+"/);
  assert.match(html, /<meta property="og:title" content="WattNot: cut GPU power per AI inference without losing quality">/);
  assert.match(html, /<meta property="og:description" content="[^"]+"/);
  assert.match(html, /<meta property="og:image" content="https:\/\/ak9381\.github\.io\/wattnot\/dash\/img\/hero-1\.png">/);
  assert.match(html, /<meta property="og:type" content="website">/);
  assert.match(html, /<meta property="og:url" content="https:\/\/ak9381\.github\.io\/wattnot\/">/);

  assert.match(html, /Was that agent call worth the watts\?/);
  assert.match(html, /Every watt checked, plus every dollar and litre\./);
  assert.match(html, /Powered by Meta Muse Spark, Glimmer, Llama and SAM 3\.1/);
  assert.match(html, /Let WattNot guide your agents/);
  assert.match(html, /href="runs\.html"/);
});

test("v2 configure flow has 'Your stack' step with four framework choices and code snippets", () => {
  const js = fs.readFileSync(path.join(dashDir, "v2.js"), "utf8");
  assert.match(js, /"Your stack"/);
  assert.match(js, /openai-python/);
  assert.match(js, /LangChain/);
  assert.match(js, /LlamaIndex/);
  assert.match(js, /plain API/);
  assert.match(js, /OpenAI\(base_url="http:\/\/localhost:8000\/v1"\)/);
  assert.match(js, /ChatOpenAI\(base_url="http:\/\/localhost:8000\/v1"\)/);
  assert.match(js, /OpenAILike\(api_base="http:\/\/localhost:8000\/v1"\)/);
  assert.match(js, /curl http:\/\/localhost:8000\/v1\/chat\/completions/);
});

test("runs and report show framework with MOCK label fallback when none recorded", () => {
  const runsJs = fs.readFileSync(path.join(dashDir, "runs.js"), "utf8");
  const reportJs = fs.readFileSync(path.join(dashDir, "report.js"), "utf8");
  assert.match(runsJs, /framework: none recorded/);
  assert.match(reportJs, /framework: none recorded/);
});
