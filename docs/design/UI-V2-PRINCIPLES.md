# WattNot UI v2: design principles

WattNot is mostly backend: it reads GPU counters, asks a planner for a change, runs quality and latency gates, waits for a person, applies, can undo, and signs a receipt. None of that is visible unless the UI shows it. These rules say how we show all of it without scaring a newcomer.

## What the research says (short)

1. **Progressive disclosure.** Show the few things most people need first, and put the rest one obvious click away. People learn a system better this way, not worse. In Carroll's "training wheels" studies, users who started with a reduced interface were 21 to 26% faster at first and later 52% faster on advanced tasks. ([NN/g, Progressive Disclosure](https://www.nngroup.com/articles/progressive-disclosure/); [NN/g, Training Wheels UI](https://www.nngroup.com/articles/training-wheels-user-interface/))
2. **Cognitive load and chunking.** Working memory holds about 4 ± 1 new chunks (Cowan 2001). Clutter, inconsistent terms and splitting a number from its explanation all cost load the user can't spend on understanding. ([NN/g, Minimize Cognitive Load](https://www.nngroup.com/articles/minimize-cognitive-load/); [Textbook of Usability, Memory and Cognitive Load](https://www.textbookofusability.com/chapter/memory-and-cognitive-load.html))
3. **Hick's law, read carefully.** More choices slow decisions, so highlight a recommended default and break hard tasks into steps. But the law is often over-applied; "fewer options" is not automatically better, and visual search matters more. ([Laws of UX, Hick's Law](https://www.oreilly.com/library/view/laws-of-ux/9781098146955/ch04.html); [Liu et al., CHI 2020, "How Relevant is Hick's Law for HCI?"](https://doi.org/10.1145/3313831.3376878))
4. **Zeigarnik and curiosity.** Unfinished work stays on people's minds. Use it honestly: show the real remaining steps of a run, never fake incompleteness. ([NN/g, Zeigarnik Effect](https://www.nngroup.com/videos/zeigarnik-effect/); [Layr reference](https://layr.design/reference/zeigarnik-effect))
5. **Peak-end rule.** People judge an experience by its most intense moment and its end. ([NN/g, Peak-End Rule](https://www.nngroup.com/articles/peak-end-rule/))
6. **Operational transparency (labor illusion).** Showing the work a system does on your behalf raises perceived value and trust, as long as it is real work. (Buell and Norton, "The Labor Illusion", *Management Science* 57(9), 2011)
7. **Calibrated trust, not maximum trust.** Explanations should be in decision terms, fitted to the user, and show uncertainty, so people neither over- nor under-trust. Audit trails help only when they are easy to read. ([C-XAI, J. Responsible Technology 2024](https://doi.org/10.1016/j.jrt.2024.100076); [Algorithmic audits and calibrated trust, CSCW 2024](https://doi.org/10.1145/3678884.3682050))
8. **"Show your work" products.** GitHub Actions and Vercel group deploy logs into collapsible steps with a status dot and duration; failed steps open by themselves. n8n and Temporal show each node or activity with its exact input and output. Stripe shows every API request with its body and response. The pattern: a calm summary row, and the raw truth one click down.
9. **Contrast.** Body text needs 4.5:1 against its background, large text 3:1 (WCAG 2.x, SC 1.4.3). Glass panels must still meet this.

## Rules for WattNot

1. **Three levels, always.** Level 1: one sentence and one number. Level 2: the steps and the plain-word reasons. Level 3: raw data (prompts, replies, JSON, hashes). Every card follows this. One *Expand all / Collapse all* control sits at the top.
2. **No hidden step.** Telemetry read, planner prompt and reply, each gate, approve, apply, undo, receipt signing, partner calls and their cost each get their own row. If the system did it, the user can open it.
3. **Calm by default.** Start with 4 to 6 top-level cards. During a run only the active stage opens; it folds back when the run moves on, like a CI log.
4. **Recommended defaults.** On Configure, every option has a preselected safe default (replay, standard profile, human approval). Advanced options sit in a collapsed group.
5. **Explanation next to the thing.** Every option has an (i) button that opens a side panel with: what it is, why it matters here, an example. No separate help page.
6. **Every number says where it came from.** MEASURED, ESTIMATE, SIMULATED, MOCK, REPLAY, ADVICE badges stay on every figure, in the same place, the same colour.
7. **Honest progress.** Show real remaining steps ("4 of 8 done"). No fake spinners or invented urgency.
8. **Design the peak and the end.** The peak is the gate result (quality held, power dropped). The end is the signed two-path report with a green "signature verified". Both get the biggest type on screen.
9. **Show the money.** Every paid call shows its cost next to it. Live GPU mode is visibly gated and off by default.
10. **Plain words.** Short sentences, concrete nouns, the unit after every number. No "seamless", "leverage", "unlock" or "revolutionary". Technical terms get a one-line gloss the first time.
11. **Bright, legible, dark.** Near-black background, white headings, cyan/teal/pink accents, big KPI numbers with small captions. Glass cards keep ≥4.5:1 text contrast.
