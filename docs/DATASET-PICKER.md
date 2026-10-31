# Dataset picker

Every saving is measured on a set of prompts with known-good answers. You choose the set on the
setup screen (Advanced settings, "Dataset savings are measured on") or in `wattnot.json`:

```json
{"dataset": "auto"}
```

| Choice | What it is | Checked by |
|---|---|---|
| `humaneval` | Public benchmark: 40 HumanEval coding problems (bundled, MIT licence) | unit tests |
| `gsm8k` | Public benchmark: 60 GSM8K maths problems (bundled, MIT licence) | expected answer |
| `mixed` (default) | WattNot's own mixed set, proof-v1: extraction, classification, code tracing, maths, logic (111 prompts) | gold answers |
| `custom` | Your upload: JSONL, one row per prompt, `prompt` plus `expected` or `tests` | your answers or tests |
| `auto` | Let WattNot decide: samples up to 200 real prompts, scores each as code, maths or general, and picks the closest set. 60% or more of one kind picks that public set; a blend stays on mixed. No model call, same answer every time. | the chosen set |

Uploads go to `POST /api/dash/v2/dataset/upload`, are checked line by line, and are stored by
sha256 on the local machine only. `POST /api/dash/v2/dataset/decide` with `{"prompts": [...]}`
returns the choice and the reason.

Every new receipt has a `dataset` block: id, kind, number of items, sha256 of the file, how it
was chosen (`user` or `wattnot`) and, for `auto`, the reason. Runs from before the picker record
`"id": "not-recorded"` rather than a guess. Older receipts without the block still validate.
