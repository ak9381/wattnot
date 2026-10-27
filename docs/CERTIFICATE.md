# Mock efficiency certificates

A certificate is a one-page, human-readable restatement of one verified receipt. Subject,
date, energy and cost saved per request with the interval, the grade, the receipt hash, and a
QR code that points at the verifier. Nothing on it is new information. Every figure is
re-derived from the receipt when it is minted and again when it is checked.

> **Every certificate is a MOCK.** It carries MOCK, NOT TRADABLE and NOT A CARBON CREDIT on the
> page, and the watermark is part of the hashed document. There is no flag to leave it off.
> No registry, buyer or regulator accepts it, and it makes no carbon claim.

This exists to show a direction (a statement a reader can trace to a signed receipt), not to
sell one.

## Try it

```bash
wattnot certificate mint --receipt verifier/vectors/real-estimate.json --out certificates
wattnot certificate verify certificates/WNC-MOCK-0001.json \
    --receipt verifier/vectors/real-estimate.json --register certificates/register.json
wattnot certificate register --register certificates/register.json
wattnot certificate roadmap
```

`mint` writes `<serial>.json`, `<serial>.html` and the register. Add `--pdf` if Chrome or
Chromium is installed (otherwise open the HTML and print to PDF). A sample is in
`data/certificate/sample/`.

A SIMULATED receipt (for example the cloud demo) is refused unless you add `--allow-simulated`.
Then the page says SIMULATED DATA.

## Mint rules

All of these are enforced in code (`src/wattnot/certificate/rules.py`), not in the renderer.

1. The receipt is verified again: schema, sha256, a signature from a trusted key, and the joules
   block. The grade comes from that check, not from what the file says about itself.
2. Grade VALID or ESTIMATE. NOISE and UNVERIFIABLE are refused. SIMULATED needs `--allow-simulated`.
3. There must be a saving: above zero and, if the receipt has an interval, an interval that
   starts above zero. The quality pass rate may not fall by more than one point.
4. One receipt, one certificate. The register stores a claim key for the receipt hash, the run
   id, and (for adapted cloud receipts) the source receipt hash, so rebuilding a receipt does not
   get a second certificate for the same saving.
5. The register is a JSON file where each entry holds the hash of the one before it. A broken
   chain stops further minting, and `certificate register` reports it.

Refusals exit with code 2 and list every rule that failed. Nothing is written.

## What it can and cannot show

- It shows that a figure can be traced: certificate hash, receipt hash, signature, register serial.
- It does not show that the run was honest. That needs an outside party (see the roadmap).
- The register is a local file. Someone could start a second register, so it blocks accidents,
  not fraud.
- Energy is GPU draw or a model of it for the listed requests, not a whole site.
- No carbon figure appears anywhere. The checker fails a certificate that uses words like
  neutral, offset or net zero.

## Roadmap (ADVICE)

This is our own reasoning. It is not legal or audit advice, and we have not studied any specific
standard yet, so we name none. A real scheme would need:

1. **A methodology outsiders have reviewed.** Today the method is ours.
2. **A baseline rule.** If anyone can pick a flattering baseline, the number means nothing.
3. **Third-party verification.** A signature shows the file was not edited, not that the run was honest.
4. **A registry that prevents double counting.** Ours is a local file.
5. **Legal review.** Whether such a statement can be traded or claimed depends on where it is used.

`wattnot certificate roadmap --json` returns the same list for a page.

JSON shapes for the frontend are in [moonshot/API-SHAPES.md](moonshot/API-SHAPES.md).
