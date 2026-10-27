"""Measurement profiles: how long a block is and how many interleaved repeats a cap gets.

RUN2 used the `quick` numbers (3 paired rounds of 2 x 111 prompts, about 7 s per block). Its
stock-vs-stock check read 8.6% apart, the first stock block 14.6 J/request and the second 15.8,
even though the 15 stock blocks inside the cap steps agreed to about 0.6% (coefficient of
variation). A short block after a cold start is one possible cause; the data cannot prove it. So
the profiles lengthen the block, repeat it more, warm the card for a fixed time first, run one
throw-away stock block, alternate which arm goes first, and give the self-check several stock
blocks so the noise is reported, not assumed.

Every number is a default for the CLI and can be overridden flag by flag. The profile is part of
the run's checkpoint fingerprint, so a resumed run cannot change method half way.

block seconds is an ESTIMATE from RUN2's rate (222 requests in about 6.9 s at the stock cap).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

RUN2_REQ_PER_S = 222 / 6.9  # MEASURED in RUN2, stock cap, 32 in flight
PROMPTS = 111


@dataclass(frozen=True)
class Profile:
    reps: int            # paired stock/cap rounds per cap (the CI uses reps - 1 degrees of freedom)
    repeat: int          # copies of the 111-prompt set in one block (block length)
    warmup: int          # untimed requests before each block
    warmup_s: float      # then keep the card busy this many seconds before metering
    burn_in: int         # unmetered stock blocks before the first measurement of a process
    counterbalance: bool  # alternate which arm goes first (A B, B A, A B ...)
    control_blocks: int  # extra stock blocks in the gate self-check (noise estimate)

    def block_seconds(self, settle_s: float = 4.0) -> float:
        """ESTIMATE: wall time of one block, settle and warm-up included."""
        return settle_s + self.warmup_s + self.warmup / RUN2_REQ_PER_S + (
            self.repeat * PROMPTS / RUN2_REQ_PER_S)

    def step_seconds(self, settle_s: float = 4.0) -> float:
        """ESTIMATE: one cap = reps x (stock block + cap block)."""
        return 2 * self.reps * self.block_seconds(settle_s)

    def as_dict(self) -> dict:
        return asdict(self)


PROFILES = {
    "quick": Profile(reps=3, repeat=2, warmup=16, warmup_s=0, burn_in=0, counterbalance=False,
                     control_blocks=0),  # what RUN2 ran
    "standard": Profile(reps=5, repeat=6, warmup=16, warmup_s=10, burn_in=1, counterbalance=True,
                        control_blocks=2),
    "long": Profile(reps=7, repeat=12, warmup=32, warmup_s=20, burn_in=1, counterbalance=True,
                    control_blocks=4),
}
DEFAULT = "standard"

# CLI flag (argparse dest) -> Profile field
FIELDS = ("reps", "repeat", "warmup", "warmup_s", "burn_in", "counterbalance", "control_blocks")


def resolve(name: str | None, **overrides) -> Profile:
    """The named profile with any explicitly given field replaced (None means "not given")."""
    base = PROFILES[name or DEFAULT]
    d = base.as_dict()
    d.update({k: v for k, v in overrides.items() if k in FIELDS and v is not None})
    return Profile(**d)


def add_args(ap) -> None:
    """The measurement flags, shared by `run` (GPU host) and `orchestrate` (control machine)."""
    import argparse

    ap.add_argument("--profile", choices=sorted(PROFILES), default=None,
                    help=f"measurement profile (default {DEFAULT}); the flags below override it")
    ap.add_argument("--reps", type=int, default=None, help="paired stock/cap rounds per cap")
    ap.add_argument("--repeat", type=int, default=None, help="prompt-set copies per block")
    ap.add_argument("--warmup", type=int, default=None, help="untimed requests before a block")
    ap.add_argument("--warmup-s", dest="warmup_s", type=float, default=None,
                    help="then keep the card busy this long before metering")
    ap.add_argument("--burn-in", dest="burn_in", type=int, default=None,
                    help="unmetered stock blocks before the first measurement")
    ap.add_argument("--counterbalance", action=argparse.BooleanOptionalAction, default=None,
                    help="alternate which arm goes first in each round")
    ap.add_argument("--control-blocks", dest="control_blocks", type=int, default=None,
                    help="extra stock blocks in the gate self-check (noise estimate)")
