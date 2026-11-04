"""Live Muse Code edit of ak9381/wattnot-demo-deploy, pushed to a NEW side branch, PR opened (never main)."""
import json, subprocess, sys, tempfile
from pathlib import Path
from wattnot.musecode.apply_pr import apply_pr, DEFAULT_MODEL

r = json.loads(Path("/tmp/wn-take-r01.json").read_text())
work = Path(tempfile.mkdtemp(prefix="wn-demo-")); repo = work / "repo"
print("cloning ak9381/wattnot-demo-deploy (read-only base: main) ...", flush=True)
if subprocess.run(["git", "clone", "-q", "https://github.com/ak9381/wattnot-demo-deploy.git", str(repo)]).returncode:
    print("clone FAILED"); sys.exit(1)
print(f"Muse Code (muse exec, {DEFAULT_MODEL}, live): set {r['rack']} power cap to {r['cap_w']} W ...", flush=True)
try:
    res = apply_pr({"power_cap_w": r["cap_w"], "saving_pct": 0.0, "p99_delta_ms": 0},
                   repo, receipt_sha256=r["sha"], out_dir=work / "out",
                   labels={"power_cap_w": "SIMULATED", "saving_pct": "SIMULATED", "p99_delta": "SIMULATED"},
                   mock=False, open_pr=True)
except Exception as e:
    print(f"Muse Code FAILED: {type(e).__name__}: {str(e)[:300]}"); sys.exit(1)
for ln in res.patch_diff.splitlines():
    if ln.startswith(("+", "-")) and not ln.startswith(("+++", "---")):
        print("    " + ln)
print(f"\n  new side branch: {res.branch_name}   (base main untouched)")
if res.pr_url:
    print(f"  PR opened: {res.pr_url}")
    Path("/tmp/wn-take-pr.txt").write_text(res.pr_url)
else:
    print("  PR NOT opened (gh pr create failed)"); sys.exit(1)
