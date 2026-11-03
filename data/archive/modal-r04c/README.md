# modal-r04c

Status: in use (modal-r1 replay base since 3 Nov)

Where it lives: branch `data/modal-full-1101`, `data/modal-full-1101/r04c (also r04c.*.events.jsonl, r04c.log)` (not moved; signatures untouched).

Why: Round approved on energy (-63.56% J/correct MEASURED) but LangChain failed (rc 5, saver_on 11/20) and hearing failed (Meta key not in the container).

Fixed by: Meta key secret: main 175509e; LangChain saver: 6101971, e3a0415, 7dcbf6f

Replaced by: its LangChain and hearing blocks were swapped for the later fixed block runs in the modal-r1 replay; r04f was the next full round
