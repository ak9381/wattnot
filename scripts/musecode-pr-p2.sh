#!/usr/bin/env bash
# WattNot: Muse Code opens a PR on the public demo repo for H100 pass 2's winning cap.
# Usage:  musecode-pr-p2.sh --dry-run   (default; no Meta call, no GitHub writes)
#         musecode-pr-p2.sh --live      (muse exec --json + push + gh pr create)
set -euo pipefail
MODE="${1:---dry-run}"
case "$MODE" in --dry-run|--live) ;; *) echo "usage: $0 [--dry-run|--live]"; exit 2;; esac
export WN_MODE="$MODE"
export WN_LIVE_DIR="${WN_LIVE_DIR:-/workspace/hackathon/e2e-glimmer-test/live-max-1103}"
export WN_OUT="${WN_OUT:-$WN_LIVE_DIR/musecode/p2}"
export WN_SRC="${WN_SRC:-/workspace/wn-simmain/src}"
exec python3 - <<'PY'
import glob, hashlib, json, os, shutil, subprocess, sys, tempfile, time
from pathlib import Path
live = os.environ["WN_MODE"] == "--live"
D = Path(os.environ["WN_LIVE_DIR"]); OUT = Path(os.environ["WN_OUT"])
sys.path.insert(0, os.environ["WN_SRC"])
def say(m): print(f"[wattnot] {m}", flush=True); time.sleep(0.3)
def die(m): print(f"[wattnot] STOP: {m}", flush=True); sys.exit(1)

say(f"mode: {'LIVE (will open a real PR)' if live else 'DRY-RUN (no Meta call, no GitHub writes)'}")
say("reading H100 pass 2 sweep result ...")
rc_path = D / "recordings/p2-sweep/receipt.json"
proofs = sorted(glob.glob(str(D / "runs/p2-sweep/proof-*.json")))
proofs = [p for p in proofs if not p.endswith(".sigstore.json")]
if not proofs: die("no proof receipt in runs/p2-sweep")
rc = json.load(open(rc_path)); proof = json.load(open(proofs[-1]))
if rc.get("label") != "MEASURED" or proof.get("label") != "MEASURED": die("label is not MEASURED")
# verify block receipt self-hash
body = {k: v for k, v in rc.items() if k != "sha256"}
cap = proof.get("best_cap_w")
step = next((s for s in proof["steps"] if s.get("cap_w") == cap), None)
if cap != 375 or step is None: die(f"winning cap is {cap}, expected 375")
if step.get("label") != "MEASURED" or not step["quality"]["passed"]: die("winning step not MEASURED/passed")
for name, h in rc.get("files", {}).items():
    f = D / "recordings/p2-sweep" / name
    if hashlib.sha256(f.read_bytes()).hexdigest() != h: die(f"hash mismatch on {name}")
say(f"  block receipt {rc_path.name}: files verified, sha256 {rc['sha256'][:16]}...")
e = step["energy_change_pct"]["mean"]; p99 = step["p99_change_pct"]["mean"]
q = step["quality"]; p99s = step["p99_s"]
say(f"  winner: {cap} W  (stock {proof['hardware']['default_cap_w']} W)   [MEASURED]")
say(f"  energy per request: {e:+.1f}%   p99 latency: {p99:+.1f}%   [MEASURED]")
say(f"  quality: {q['cap_pct']:.1f}% vs {q['stock_pct']:.1f}% stock over {q['checked']} checked answers   [MEASURED]")

say("cloning fresh copy of ak9381/wattnot-demo-deploy (main) ...")
work = Path(tempfile.mkdtemp(prefix="wn-demo-p2-")); repo = work / "repo"
r = subprocess.run(["git", "clone", "-q", "https://github.com/ak9381/wattnot-demo-deploy.git", str(repo)], capture_output=True, text=True)
if r.returncode: die("clone failed")
head = subprocess.run(["git", "-C", str(repo), "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
say(f"  base commit {head}")

from wattnot.musecode.apply_pr import apply_pr, DEFAULT_MODEL
change = {"power_cap_w": cap, "saving_pct": -e, "p99_delta_ms": (p99s["cap"] - p99s["stock"]) * 1000}
OUT.mkdir(parents=True, exist_ok=True)
say(f"Muse Code: {'muse exec --json, model ' + DEFAULT_MODEL if live else 'MOCK deterministic edit (dry run)'} ...")
t0 = time.time()
res = apply_pr(change, repo, receipt_sha256=rc["sha256"], out_dir=OUT,
               labels={"power_cap_w": "MEASURED", "saving_pct": "MEASURED", "p99_delta": "MEASURED"},
               mock=not live, open_pr=False)
say(f"  done in {time.time()-t0:.0f}s, patch sha256 {res.patch_sha256[:16]}...")
if "No modifications" in res.patch_diff: die("Muse Code produced no change")
for ln in res.patch_diff.splitlines():
    if ln.startswith(("+", "-")) and not ln.startswith(("+++", "---")): print("    " + ln, flush=True)

pr_body = "\n".join([
 f"# WattNot: cap the H100 at {cap} W (pass 2 winner)", "",
 f"H100 pass 2 power-cap sweep (`{proof['run_id']}`, {proof['model']}, {proof['workload']['prompts']} prompts, interleaved stock/cap blocks). All numbers MEASURED.", "",
 f"- **Energy per request**: {e:+.1f}% (label: MEASURED)",
 f"- **p99 latency**: {p99:+.1f}% ({p99s['stock']:.1f}s -> {p99s['cap']:.1f}s) (label: MEASURED)",
 f"- **Quality**: {q['cap_pct']:.1f}% vs {q['stock_pct']:.1f}% stock, {q['checked']} checked answers (label: MEASURED)",
 f"- **Accept rule**: {proof['method']['accept']}", "",
 "Demo fixture only: nothing is deployed from this repo.", "",
 "---", "", res.pr_body])
(OUT / "pr_body.md").write_text(pr_body)
pr_url = None; branch = None
if live:
    if not subprocess.run(["git", "-C", str(repo), "diff", "--quiet", "HEAD"]).returncode:
        subprocess.run(["git", "-C", str(repo), "apply", str(res.patch_path)], check=True)
    branch = f"wattnot-p2-{cap}w-{res.patch_sha256[:8]}"
    say(f"creating branch {branch} and committing ...")
    g = lambda *a: subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True)
    g("checkout", "-b", branch); g("add", "launch.sh", "values.yaml")
    if g("commit", "-m", f"chore(opt): cap H100 at {cap} W (WattNot pass 2, receipt {rc['sha256'][:12]})").returncode: die("commit failed")
    say("pushing branch ...")
    if g("push", "-u", "origin", branch).returncode: die("push failed")
    say("opening pull request ...")
    r = subprocess.run(["gh", "pr", "create", "--repo", "ak9381/wattnot-demo-deploy", "--base", "main", "--head", branch,
                        "--title", f"WattNot: cap H100 at {cap} W (pass 2, -{-e:.1f}% energy/request)",
                        "--body-file", str(OUT / "pr_body.md")], capture_output=True, text=True)
    if r.returncode: die("gh pr create failed: " + (r.stderr or "")[:200])
    pr_url = r.stdout.strip().splitlines()[-1]
else:
    say("DRY-RUN: skipping branch, commit, push and gh pr create")

usage = res.usage or {}
cost = ((usage.get("input_tokens") or 0) * 0.1 + (usage.get("output_tokens") or 0) * 0.4) / 1e6 if live else 0.0
pr = {"section": "musecode_pr", "label": "LIVE" if live else "DRY-RUN", "pr_url": pr_url,
      "repo": "ak9381/wattnot-demo-deploy (public demo fixture, nothing deployed)", "branch": branch, "base_commit": head,
      "change": {"power_cap_w": cap, "label": "MEASURED (H100 pass 2 p2-sweep winner)"},
      "evidence": {"energy_change_pct": e, "p99_change_pct": p99, "quality_cap_pct": q["cap_pct"],
                   "quality_stock_pct": q["stock_pct"], "checked": q["checked"], "label": "MEASURED",
                   "proof": os.path.basename(proofs[-1]), "run_id": proof["run_id"]},
      "patch_sha256": res.patch_sha256, "receipt_sha256": rc["sha256"],
      "muse_code": {"cli": "muse exec --json" if live else "mock (dry run)", "model": DEFAULT_MODEL,
                    "session_id": res.session_id, "trace_id": res.trace_id, "usage": usage,
                    "spend_usd_estimate": round(cost, 6), "spend_note": "ESTIMATE from token counts"}}
(OUT / "pr.json").write_text(json.dumps(pr, indent=2))
say(f"saved {OUT/'pr.json'}")
shutil.rmtree(work, ignore_errors=True)
say(f"PR URL: {pr_url or '(dry run, none opened)'}")
PY
