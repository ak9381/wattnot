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
- Signed offline, no Rekor transparency-log entry yet, so the verifier caps the grade at ESTIMATE.
- The receipt's own noise test runs Welch's t on per-request energy across 60 different prompts (80 to 1,669 J each). That spread is large, so the 95% CI of the saving (-0.5% to +24.0%, mean 12.6% on per-request attributed energy) crosses zero and the grade is NOISE, billed saving 0. The 12.49% headline is the arm total from the energy counter; the paired answer check in the same run (4 interleaved 700/375 W block pairs, 111 prompts) measured -25.3% energy and p99 +4.7%.
- `p99_ms` is 0 in this receipt as a placeholder: the gateway runner did not record per-request latency for this run (noted in each arm's quality block).
- Water and carbon are ESTIMATE (0.36 L/kWh LBNL 2024; 727 gCO2/kWh CEA India FY2023-24).
