# Verify the signed H100 Text 60 receipt and EU report

One command, from a fresh clone of this branch (Python 3.9+, standard library only, no install, no network):

```bash
python3 verifier/wattnot_verify.py receipts/h100-1104/rcpt_20261104-032655-text.json --trust verifier/trusted-keys.json
```

You should see `ok` for schema, sha256, signature (`offline:d3a16ddd216502c0 [signed (WattNot certificate key)]`), joules, sci and labels. Change any number in the receipt and the sha256 and signature steps fail.

What is in here:
- `data/h100-text60-1104/sequential.run.json`: the raw run record from the H100 (run `seq-20261104-032655-text`, Nov 4, 2026). Arm totals from the NVML energy counter: 72,896.7 J without WattNot (700 W) vs 63,793.3 J with WattNot (375 W), 12.49% less, 54/60 correct in both arms (MEASURED).
- `runs/seq-20261104-032655-text/run.json`: the run summary the receipt builder reads (per-request joules from the same run).
- `receipts/h100-1104/rcpt_20261104-032655-text.json` + `.sigstore.json`: the receipt and its Ed25519 signature bundle.
- `reports/eu/eu_report_rcpt_20261104-032655-text.html` / `.csv`: the EU 2024/1364 field export built from that receipt.
- `verifier/wattnot-report-signing-2026-11-04.pub.hex`: the public key (also listed in `verifier/trusted-keys.json`). The private key is not in git.

Read the grade honestly:
- Every receipt below is also in the public Rekor transparency log (rekor.sigstore.dev, `rekord` entry, Ed25519 key above, signed content = the canonical receipt whose sha256 is `integrity.sha256`). `--rekor-online` with `wattnot verify` looks the entry up live.
- The receipt's own noise test runs Welch's t on per-request energy across 60 different prompts (80 to 1,669 J each). That spread is large, so the 95% CI of the saving (-0.5% to +24.0%, mean 12.6% on per-request attributed energy) crosses zero and the grade is NOISE, billed saving 0. The 12.49% headline is the arm total from the energy counter; the paired answer check in the same run (4 interleaved 700/375 W block pairs, 111 prompts) measured -25.3% energy and p99 +4.7%.
- `p99_ms` is 0 in this receipt as a placeholder: the gateway runner did not record per-request latency for this run (noted in each arm's quality block).
- Water and carbon are ESTIMATE (0.36 L/kWh LBNL 2024; 727 gCO2/kWh CEA India FY2023-24).

## All signed H100 runs from Nov 4, 2026 (MEASURED, Muse Glimmer 30B, vLLM, 700 W vs 375 W)

Check any of them the same way, or all at once:

```bash
for f in $(ls receipts/h100-1104/rcpt_*.json | grep -v sigstore); do python3 verifier/wattnot_verify.py "$f" --trust verifier/trusted-keys.json | grep -E "rekor|grade"; done
```

| Run | Arm totals (NVML counter) | Saving | Receipt grade | Rekor |
|---|---|---|---|---|
| seq-20261104-000651-text | 72,877 -> 63,595 J | 12.74% | NOISE | [3686499383](https://search.sigstore.dev/?logIndex=3686499383) |
| seq-20261104-002729-agent | 33,185 -> 30,750 J | 7.34% | NOISE | [3686499462](https://search.sigstore.dev/?logIndex=3686499462) |
| seq-20261104-011725-images | 40,410 -> 30,367 J | 24.85% | VALID | [3686499505](https://search.sigstore.dev/?logIndex=3686499505) |
| seq-20261104-013308-images | 40,564 -> 30,478 J | 24.87% | VALID | [3686499560](https://search.sigstore.dev/?logIndex=3686499560) |
| seq-20261104-022905-text | 72,585 -> 63,558 J | 12.44% | NOISE | [3686499629](https://search.sigstore.dev/?logIndex=3686499629) |
| seq-20261104-031142-text (20 req) | 24,121 -> 21,189 J | 12.15% | NOISE | [3686499695](https://search.sigstore.dev/?logIndex=3686499695) |
| seq-20261104-032655-text | 72,897 -> 63,793 J | 12.49% | NOISE | [3686498857](https://search.sigstore.dev/?logIndex=3686498857) |
| seq-20261104-034921-agent | 33,207 -> 29,073 J | 12.45% | NOISE | [3686499713](https://search.sigstore.dev/?logIndex=3686499713) |
| seq-20261104-040850-voice | 399,347 -> 423,976 J | -6.17% (used MORE) | NOISE | [3686499805](https://search.sigstore.dev/?logIndex=3686499805) |

How to read it:
- VALID needs MEASURED + a signature + a Rekor entry + a significant noise test. The images runs pass. Text, agent and voice are NOISE because the receipt's Welch test compares single requests across very different prompts (tens to thousands of joules each), so the per-request CI crosses zero even when the arm totals differ by ~12%.
- The receipt's per-request mean is not the arm total: the per-request numbers are each request's attributed share, and the WattNot arm includes the gateway's gate warm-up requests. For seq-20261104-034921-agent this flips the sign (per-request mean -8.9%, arm total +12.45% saved); the headline uses the arm totals from the energy counter.
- Each raw run record (data/h100-seq-1104/, data/h100-text60-1104/) also holds that run's paired answer check (4 interleaved 700/375 W block pairs, 111 prompts), hash-bound into its receipt. The answer check has no separate receipt: per-block energy was not logged, only per-pair percentages.
- Skipped: seq-20261104-050330-text (stopped at 19 requests, the WattNot arm never ran). The brief-answers take is not in these logs yet.
