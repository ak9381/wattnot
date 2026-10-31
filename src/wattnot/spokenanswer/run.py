"""Run the watts-per-spoken-answer gate once and write its result.

    python -m wattnot.spokenanswer.run --fake --out runs/t1 --budget-s 90

``--fake`` uses the offline answerer (ground truth, no network, not a
measurement): it exercises the plumbing inside the 2-hour Glimmer test without
a launch or a paid call. A live run passes ``--base-url`` to reach the served
model (Glimmer), which answers the *transcribed* questions (the transcripts are
cached from Muse Voice Transcribe; no transcription happens here). ``--budget-s``
caps the wall-clock the gate may take so it always fits the test window;
recordings left unanswered by the cut count as misses.

Alongside the pass/fail, the summary reports joules per correct spoken answer
next to joules per token and joules per correct (text) answer. On a live metered
H100 run those are MEASURED; ``--fake`` has no meter, so they are labelled MOCK
from the assumed Wh passed on the command line.

Exit code 0 when every spoken answer is correct, 1 on any miss or error, so the
block driver sees a wrong spoken answer as a failing block, just like the eye
test.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from wattnot.spokenanswer.assets import load_context, load_spoken_set, load_transcripts
from wattnot.spokenanswer.client import (
    DEFAULT_MODEL,
    REASONING_MAX_COMPLETION_TOKENS,
    OfflineAnswerer,
    ServedSpokenAnswerer,
    answer_spoken_set,
)
from wattnot.spokenanswer.gate import spoken_answer_gate, spoken_energy

# Assumed per-answer energy used only by the --fake runner, which has no meter:
# enough to fill the three joules figures as MOCK so the panel has something to
# show. A live run reads the real Wh from the metered sweep (MEASURED).
FAKE_WH_PER_ANSWER = 0.35
FAKE_TOKENS_PER_ANSWER = 6.0
FAKE_J_PER_CORRECT_ANSWER = 42.0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="WattNot watts-per-spoken-answer gate")
    ap.add_argument("--assets", default=None, help="spoken asset dir (default: data/voice/spoken)")
    ap.add_argument("--out", default=None, help="write spoken_answer.json here")
    ap.add_argument("--budget-s", type=float, default=None, help="wall-clock budget for the gate")
    ap.add_argument("--fake", action="store_true", help="offline answerer (no network, not measured)")
    ap.add_argument("--base-url", default=None, help="served model base URL (live only)")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--wh-per-answer", type=float, default=None,
                    help="measured Wh per spoken answer (live); defaults to a MOCK value in --fake")
    ap.add_argument("--tokens-per-answer", type=float, default=None,
                    help="tokens per spoken answer, for joules per token")
    ap.add_argument("--j-per-correct-answer", type=float, default=None,
                    help="the run's joules per correct (text) answer, carried through unchanged")
    ap.add_argument("--concurrency", type=int, default=8,
                    help="answer the recordings in parallel (live): fit the budget on a reasoner")
    ap.add_argument("--max-completion-tokens", type=int, default=REASONING_MAX_COMPLETION_TOKENS,
                    help="completion-token cap per answer (reasoning models need room)")
    args = ap.parse_args(argv)

    spoken = load_spoken_set(args.assets, require_files=not args.fake)
    if args.fake or not args.base_url:
        answerer = OfflineAnswerer(spoken)
        mode = "fake"
    else:
        transcripts = load_transcripts(args.assets)
        # the rack-scene telemetry the questions ask about, given to the model as a system message
        answerer = ServedSpokenAnswerer(transcripts, model=args.model, base_url=args.base_url,
                                        context=load_context(args.assets),
                                        max_completion_tokens=args.max_completion_tokens)
        mode = "live"

    answers, run_meta = answer_spoken_set(spoken, answerer, budget_s=args.budget_s,
                                          concurrency=args.concurrency)

    rows = spoken_answer_gate(answers, spoken.items).detail["items"]
    pass_rate = (sum(1 for r in rows if r["ok"]) / len(rows)) if rows else 0.0
    wh = args.wh_per_answer if args.wh_per_answer is not None else (
        FAKE_WH_PER_ANSWER if mode == "fake" else None)
    energy = None
    if wh is not None:
        # On a live (metered) run we never invent a token count: if
        # --tokens-per-answer is omitted, j_per_token stays None rather than
        # stamping the MOCK fallback with a MEASURED label. The fake runner,
        # which has no meter, keeps the MOCK placeholder so the panel fills.
        energy = spoken_energy(
            wh_per_answer=wh,
            tokens_per_answer=(args.tokens_per_answer if args.tokens_per_answer is not None
                               else (FAKE_TOKENS_PER_ANSWER if mode == "fake" else None)),
            pass_rate=pass_rate,
            label="MOCK" if mode == "fake" else "MEASURED",
            j_per_correct_answer=(args.j_per_correct_answer if args.j_per_correct_answer is not None
                                  else (FAKE_J_PER_CORRECT_ANSWER if mode == "fake" else None)),
        )

    result = spoken_answer_gate(answers, spoken.items, energy=energy)

    summary = {
        "spec": "wattnot.spokenanswer/1",
        "mode": mode,
        "model": args.model,
        "transcriber": spoken.transcriber,
        "label": "SYNTHETIC",   # the test items (images / audio) are synthetic
        "answers_label": "MOCK" if mode == "fake" else "MEASURED",   # served-model answers
        "passed": result.passed,
        "reason": result.reason,
        "run": run_meta,
        "detail": result.detail,
    }
    if args.out:
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        (out / "spoken_answer.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

    miss = result.detail.get("missed") or []
    print(f"spoken answer [{mode}]: {result.reason}"
          + (f" (budget {args.budget_s}s hit)" if run_meta.get("budget_hit") else ""))
    if energy is not None:
        print(f"  energy [{energy['label']}]: {energy['j_per_correct_spoken']} J/correct spoken, "
              f"{energy['j_per_token']} J/token, {energy.get('j_per_correct_answer')} J/correct answer")
    for row in result.detail.get("items", []):
        if not row["ok"]:
            print(f"  MISS {row['id']} ({row['noise']}): expected {row['expected']!r} got {row['got']!r}")
    return 0 if result.passed and not miss else 1


if __name__ == "__main__":
    raise SystemExit(main())
