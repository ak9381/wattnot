# modal-r04e-langchain

Status: superseded

Where it lives: branch `data/modal-full-1101`, `data/modal-full-1101/attachments/r04e/ (r04e-langchain-20261102T071628Z failed: Modal client disconnect; r04e-langchain-20261102T081001Z 20/20 both arms)` (not moved; signatures untouched).

Why: Block-only LangChain reruns attached to r04e. The first died on a missed heartbeat; the second passed but belongs to the rejected r04e round.

Fixed by: detached block runs (fix/detach-block-run); fc-tune 95b4431

Replaced by: r04f-langchain-20261102T112336Z (modal-r1)
