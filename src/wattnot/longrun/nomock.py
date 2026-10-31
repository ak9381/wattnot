"""No-mock preflight: refuse a live run that would stand on mock / placeholder / stub / replay data.

    python -m wattnot.longrun.nomock [--model glimmer-test|glimmer|llama] [--out DIR]

This runs automatically at the start of every ``--live`` driver run
(``wattnot.longrun.driver.live_run``) and by ``scripts/e2e/ctl.py launch``. The owner's hard rule
is that nothing in the 2 h test or the long run may be mocked, faked, SIMULATED, placeholder or
stubbed — every live number must be MEASURED on the real H100 / Muse Glimmer, or come from a real
Meta API call. This guard enforces it before any VM time is spent.

It fails (exit 3) if, on the LIVE path:

* a block's live command carries a mock switch: ``--fake``, ``--dry-run``, ``--sam replay`` /
  ``replay``, ``--auto-approve-as-proof``, ``--mock``; a live sweep with ``--auto-approve`` (it must use
  ``--approval-policy``, set by a named operator, so the receipt says who authorised it);
* the eye-test / hotspot images are still placeholders (manifest ``source`` is placeholder /
  offline / synthetic / simulated / stub, or an image file is missing);
* a spoken-answer WAV is a stub (< 10 KB, or the manifest ``source`` is a stub / placeholder);
* a block references a MOCK certificate or an obvious replay data path.

The dry (``--fake``) commands are NOT checked — rehearsal is allowed to be mock; only the live path
must be real. Findings are returned as data (and written to ``nomock-preflight.json``) so the driver
and ctl.py can print them and stop.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Switches that mean "not a real measurement". Plain --auto-approve is deliberately NOT here.
BANNED_FLAGS = ("--fake", "--dry-run", "--auto-approve-as-proof", "--mock", "--simulate")
# Honest asset-source values that are acceptable for a live run.
REAL_SOURCES = ("measured", "real", "real-tts", "tts", "generated-meta", "live", "h100-run1")
# Asset-source values that are clearly not real.
MOCK_SOURCES = ("", "placeholder", "placeholder-offline", "offline", "synthetic", "simulated",
                "stub", "stub-offline", "mock", "replay")
MIN_REAL_WAV_BYTES = 10 * 1024


def _banned_tokens(argv: list[str]) -> list[str]:
    """Mock switches present in a live command line (handles --sam replay and bare replay too)."""
    hits: list[str] = []
    # look inside `bash -c "..."` strings too, so a mock switch cannot hide in a shell line
    import shlex
    flat: list[str] = []
    for tok in argv:
        if " " in tok:
            try:
                flat += shlex.split(tok)
            except ValueError:
                flat.append(tok)
        else:
            flat.append(tok)
    argv = flat
    # orchestrate's --sam defaults to replay: a live orchestrate run must ask for live SAM
    if any("wattnot.proof.orchestrate" in t for t in argv) and "--mode" in argv \
            and argv[argv.index("--mode") + 1:argv.index("--mode") + 2] == ["live"]:
        if not any(argv[i] == "--sam" and argv[i + 1:i + 2] == ["live"] for i in range(len(argv))):
            hits.append("--sam replay (default)")
        # an approval must come from a person or an operator-set policy, never a bare flag
        if "--auto-approve" in argv:
            hits.append("--auto-approve (use --approval-policy: an operator-set policy)")
        # a live wonder sweep must make REAL Wonder calls: without --wonder-live the planner is the
        # deterministic mock "muse-wonder-mock" (RUN3: the cap choice was mocked while the driver
        # thought the whole live path was real). The measurements stay real; the planner must not
        # be mocked, so fail the preflight here.
        if "--planner" in argv \
                and argv[argv.index("--planner") + 1:argv.index("--planner") + 2] == ["wonder"] \
                and "--wonder-live" not in argv:
            hits.append("--planner wonder without --wonder-live (planner would be muse-wonder-mock)")
    for i, tok in enumerate(argv):
        low = tok.lower()
        if low in BANNED_FLAGS or any(low.startswith(f + "=") for f in BANNED_FLAGS):
            hits.append(tok)
        if low == "replay":
            prev = argv[i - 1].lower() if i else ""
            hits.append("--sam replay" if prev == "--sam" else "replay")
        if "certificate" in low and any("mock" in a.lower() for a in argv):
            hits.append("mock-certificate")
    return hits


def _source_ok(source: str) -> bool:
    s = (source or "").strip().lower()
    if not s or s in MOCK_SOURCES:
        return False
    if any(w in s for w in ("mock", "stub", "placeholder", "replay", "fake", "simulat",
                            "synthetic", "offline")):
        return False   # a real-looking prefix never launders a mock word ("live-replay-...")
    if s.startswith("tts"):            # e.g. TTS-espeak-ng, TTS-pico2wave
        return True
    return any(s == good or s.startswith(good) for good in REAL_SOURCES)
    # anything else (unknown provenance) is treated as not-real: a live run must prove it


def _check_eye_assets(findings: list[dict]) -> None:
    from wattnot.vision.assets import DEFAULT_ASSETS_DIR as EYE_DIR
    man_path = EYE_DIR / "answers.json"
    try:
        man = json.loads(man_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        findings.append({"where": str(man_path), "what": "eye-test manifest missing",
                         "fix": "restore data/vision/eyetest/answers.json"})
        return
    src = str(man.get("source", ""))
    if not _source_ok(src):
        findings.append({
            "where": f"{man_path}:source", "what": f"eye-test images are not real (source={src!r})",
            "fix": "with his okay, run `WATTNOT_GEN_VISION_GO=1 META_API_KEY=... python "
                   "scripts/gen_vision_assets.py` (writes source=GENERATED-META and the real images)"})
    for it in man.get("items", []):
        p = EYE_DIR / str(it.get("file", ""))
        if not p.exists():
            findings.append({"where": str(p), "what": f"eye-test image missing for {it.get('id')!r}",
                             "fix": "generate the real eye-test images (gen_vision_assets.py)"})


def _check_spoken_assets(findings: list[dict]) -> None:
    from wattnot.spokenanswer.assets import DEFAULT_ASSETS_DIR as SPOKEN_DIR
    man_path = SPOKEN_DIR / "answers.json"
    try:
        man = json.loads(man_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        findings.append({"where": str(man_path), "what": "spoken-answer manifest missing",
                         "fix": "restore data/voice/spoken/answers.json"})
        return
    src = str(man.get("source", ""))
    if not _source_ok(src):
        findings.append({
            "where": f"{man_path}:source",
            "what": f"spoken-answer WAVs are not real audio (source={src!r})",
            "fix": "run `python scripts/gen_spoken_assets.py` with an offline TTS on PATH "
                   "(espeak-ng/espeak/pico2wave/flite); it writes real WAVs and source=TTS-<engine>"})
    for it in man.get("items", []):
        p = SPOKEN_DIR / str(it.get("file", ""))
        if not p.exists():
            findings.append({"where": str(p), "what": f"spoken WAV missing for {it.get('id')!r}",
                             "fix": "generate the real WAVs (gen_spoken_assets.py)"})
        elif p.stat().st_size < MIN_REAL_WAV_BYTES:
            findings.append({
                "where": str(p),
                "what": f"spoken WAV is a stub ({p.stat().st_size} B < {MIN_REAL_WAV_BYTES} B)",
                "fix": "synthesize the real audio from the question text (gen_spoken_assets.py)"})


def audit_plan(model: str = "glimmer-test", out: Path | None = None,
               plan_name: str = "full") -> dict:
    """Audit the LIVE path of a plan. Returns {ok, model, findings[], checked[]}."""
    from wattnot.longrun.driver import plan

    blocks = plan(out or Path("."), model, plan_name)
    findings: list[dict] = []
    checked: list[str] = []
    uses_eye = uses_spoken = False
    for b in blocks:
        argv = b.live or []
        checked.append(b.id)
        for tok in _banned_tokens(argv):
            findings.append({
                "where": f"block {b.id} (live)", "what": f"live command uses a mock switch: {tok}",
                "fix": "remove the switch; the live command must be the real one"})
        joined = " ".join(argv)
        if "wattnot.vision.run" in joined or "glimmer_probe.sh" in joined:
            uses_eye = True
        if "wattnot.spokenanswer.run" in joined:
            uses_spoken = True
    if uses_eye:
        _check_eye_assets(findings)
    if uses_spoken:
        _check_spoken_assets(findings)
    return {"ok": not findings, "model": model, "findings": findings, "checked": checked}


def write_report(report: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="WattNot no-mock preflight")
    ap.add_argument("--model", choices=["glimmer", "llama", "glimmer-test"], default="glimmer-test")
    ap.add_argument("--plan", choices=["full", "retest"], default="full")
    ap.add_argument("--out", type=Path, default=None, help="write nomock-preflight.json here")
    a = ap.parse_args(argv)
    report = audit_plan(a.model, a.out, plan_name=a.plan)
    if a.out:
        write_report(report, a.out / "nomock-preflight.json")
    if report["ok"]:
        print(f"no-mock preflight OK: {a.model} live path is all real "
              f"({len(report['checked'])} blocks checked)")
        return 0
    print(f"no-mock preflight FAILED for {a.model}: {len(report['findings'])} finding(s)",
          file=sys.stderr)
    for f in report["findings"]:
        print(f"  - {f['where']}: {f['what']}\n      fix: {f['fix']}", file=sys.stderr)
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
