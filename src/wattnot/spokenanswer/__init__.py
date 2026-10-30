"""Watts per spoken answer (the "spoken-answer gate").

A fixed set of ~20 SYNTHETIC recorded questions (clean and data-center-noise
takes) with known answers lives in the repo (``data/voice/spoken``). Muse Voice
Transcribe transcribes them once (cached in ``transcripts.json``); after a
candidate serving change the served model (Glimmer) answers the transcribed
questions, and we report joules per correct spoken answer (MEASURED on the
H100) next to joules per token and joules per correct answer. A wrong spoken
answer rejects the change just like the eye test. The gate returns the same
``GateResult`` shape as the other gates and plugs into ``wattnot.gates.decide``
and the keep/reject loop.
"""

from wattnot.spokenanswer.assets import (
    DEFAULT_ASSETS_DIR,
    SpokenItem,
    ground_truth,
    load_spoken_set,
    load_transcripts,
)
from wattnot.spokenanswer.gate import grade_answers, spoken_answer_gate, spoken_energy

__all__ = [
    "DEFAULT_ASSETS_DIR",
    "SpokenItem",
    "grade_answers",
    "ground_truth",
    "load_spoken_set",
    "load_transcripts",
    "spoken_answer_gate",
    "spoken_energy",
]
