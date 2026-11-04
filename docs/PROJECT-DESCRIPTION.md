# WattNot: AI that cuts the power and water bill of every AI answer, without making the model dumber

## The one-line version

Every time someone asks an AI a question, a GPU somewhere draws hundreds of watts and a cooling system evaporates water to keep it from overheating. WattNot is an AI agent that sits on top of your inference stack and lowers that cost per answer. It tunes the GPUs and the serving setup, checks every change against your real prompts so quality never slips, and hands you a signed receipt for every watt it saves. You only pay us when the savings are real.

## The problem, plainly

Power is now the bottleneck for AI. It's not chips and it's not talent anymore. New data centers are waiting years for grid connections. Utilities are delaying projects. Operators are paying more for electricity every quarter, and in hot, dry places, the water used for cooling is turning into a political issue as well as a cost.

Even so, most AI inference still runs the way it shipped. GPUs sit at their stock power limit, which on an H100 is 700 watts, all day, whether the workload needs it or not. Batch sizes, KV cache settings and precision are set once, by someone in a hurry, and never touched again. There's a lot of energy left on the table.

Everyone in infrastructure knows this. So why doesn't anyone fix it?

Because nobody wants to be the person who made the model worse. If you lower a power cap and latency spikes at peak hour, or you switch to FP8 and the answers get subtly wrong, you've broken production to save a few percent on the power bill. That trade is a bad one for any engineer, so the safe choice is to change nothing. The waste stays.

And even when a team does save energy, they can't prove it. The savings claim lives in a slide deck. Finance doesn't trust it, auditors can't check it, and regulators now want real numbers.

That last part matters more every month. Under the EU Energy Efficiency Directive, data centers of 500 kW or more have to report their energy and water use every year, and the CSRD sustainability rules are pushing similar reporting into company accounts. Today that reporting is mostly spreadsheets, estimates and vendor PDFs. Operators are stuck between three demands at once. They have to cut waste, they can't risk quality, and they have to prove the result to someone who doesn't trust them.

## Why today's tools don't solve it

The tools that exist each solve one slice of the problem, and none of them talk to each other.

- **Facility and DCIM tools** watch racks, temperatures and power distribution. They can tell you a rack is running hot. They have no idea which model is running on it, or whether its answers are any good.
- **GPU power tools**, plus the cap and clock settings built into the drivers, can cut watts. But they don't measure what that does to answer quality. Saving power is easy if you don't care whether the answers are still right.
- **Energy dashboards and carbon trackers** estimate emissions and show charts. They report on the waste, but they don't remove it, and their numbers usually come from estimates, not measurements.
- **Model serving tools** like vLLM have great knobs for batching, caching and precision. But they're tuned for speed and cost per token, not energy, and they don't tell you which setting is safe for your workload.

So the rack team, the GPU team, the ML team and the sustainability team each see one piece. Nobody sees the whole chain from the server room, to the GPU, to the model, to the actual answer a user gets. Without that full chain, you can't safely make the changes that matter.

## What WattNot is

WattNot is one agent that connects that whole chain and closes the loop.

1. **It looks at the room.** Snap a photo of a rack and SAM 3.1 segments it and counts the servers in it. It does not map hotspots or link racks to GPUs; it is a photo-based hint, not a measurement.
2. **It reads the GPUs.** WattNot pulls live telemetry through NVIDIA's management library: power draw, power cap, utilization, memory and temperature. It knows exactly what each GPU is doing right now.
3. **It tries one change at a time.** The AI operator (Muse Spark) or a scripted sweep proposes a single, specific change. It might lower the power cap from 700 W to 375 W, change the batch size, adjust the KV cache, turn on prefix caching or switch to FP8. Changing one thing at a time means we always know what caused what.
4. **It checks quality on your real prompts.** Before anything is kept, WattNot runs your actual workload through the changed setup and checks three things: answer quality, p99 latency, and a statistical noise test (Welch's t-test) to make sure the saving is real and not luck.
5. **It refuses and rolls back bad changes, automatically.** If quality drops, latency breaks the limit, or the saving is just noise, the change is refused and rolled back. A fixed rule makes this call, not the AI, so the model can never talk its way past a failed gate.
6. **It proves what it kept.** Every kept change turns into a receipt that records the before and after numbers, the workload, the gate results and the hardware. Receipts are hash-chained and signed with Ed25519. Run `wattnot verify` and it re-checks everything. Edit even one number and verification fails.
7. **It reports and ships.** The receipts feed a one-click EU-style efficiency report covering energy and water (water is an ESTIMATE). Muse Code then drafts the winning settings as a sandboxed pull request for review, so the change goes through your normal code review like any other change.

## Real results, not estimates

We tested WattNot on a real H100 GPU running Meta's open-weights Muse Glimmer 30B on vLLM, with live traffic going through the WattNot gateway.

- **Headline: 12.9% less energy per answer** (1,124.5 J to 979.6 J per answer, MEASURED). That's a power cap of 375 W against the stock 700 W.
- **Quality held:** 54 of 60 correct answers in both the baseline and the tuned setup.
- **Statistically real:** Welch p = 0.027, so it's not a lucky run.
- **A sealed GSM8K holdout** on the H100 (Nov 3) showed 26.65% less energy per request at 375 W plus low reasoning effort, with accuracy unchanged at 92.0% in both arms (MEASURED). We lead with the more conservative live-traffic number.
- **The safety net works.** When a scripted one-lever sweep tried FP8 on the H100, the quality gate failed and the change was refused and rolled back automatically. We show that in the demo on purpose, because a tool that only ever says "yes" isn't one you can trust.

The key metric is joules per correct answer, not joules per token. Joules per token rewards a model that gets faster by getting worse. Joules per correct answer only goes down when you save energy *and* keep the answers right.

Every number in our materials carries a label: MEASURED, ESTIMATE, SIMULATED or REPLAY. We never pass off a simulation as a measurement.

## Two ways to use it: a product and a testing tool

**1. As a product (Plugin mode).** WattNot runs as a gateway in front of your vLLM servers. Your apps send requests the same way they always have, since the gateway is OpenAI-compatible. Behind it, WattNot watches live traffic, tests changes carefully, keeps the ones that pass, and keeps improving as your traffic changes. It also includes an effort router that sends easy requests down a cheaper path and saves the heavy settings for hard ones, plus a context saver that trims long conversations so they don't burn tokens for nothing.

**2. As a testing tool (Test mode).** Before you ship a new model, a new quantization or a new GPU setting, run WattNot on a sample of your real prompts. It works like a CI check for efficiency. It tells you which changes save energy without hurting quality or latency, and which ones to refuse, with a receipt for each result. Teams can use it this way without changing production at all, which makes it an easy first step.

Both modes produce the same signed receipts and the same reports.

## What you actually get in the product

- **A live decision graph.** You can watch the agent work in real time: telemetry coming in, a change proposed, the quality gate checking it, then the result kept or rolled back. Every step lights up as it happens. Past runs can be replayed step by step.
- **Compare screens.** Baseline and tuned setups side by side, with energy, quality and latency for each lever.
- **Energy saved by lever.** A breakdown of what the power cap, batching, KV cache and FP8 each saved, or why each one was refused.
- **A savings frontier chart.** This chart plots energy against quality so you can see the best trade-off for your workload.
- **Water per inference.** An ESTIMATE of cooling water, derived from the measured energy with stated L/kWh assumptions.
- **The rack hotspot map** from your room photo (photo-based; heat layer SIMULATED).
- **A one-click EU-style efficiency report** built from signed receipts.
- **Spoken Q&A.** Ask "what did we save this week and why?" out loud and get an answer grounded in the receipts.
- **A draft pull request for the winning change**, for your normal review.
- **Replay for anyone.** Judges, auditors or a new teammate can replay every recorded run with no API keys at all, or try it live.

## How we stand out

We haven't found another tool that does all four of these together:

1. **It connects the whole chain.** Rack, GPU, model and answer live in one place: the telemetry, the serving settings and the quality check feed the same decision, and the room photo adds a photo-based hint about where to look. Other tools each own one of these pieces.
2. **Quality is a hard gate, not a hope.** Every change is tested on your real prompts and must pass quality, p99 latency and a statistics check. Bad changes roll back on their own. Power tools cut watts. We cut watts *safely*.
3. **Every saving is provable.** Signed, hash-chained receipts that anyone can verify and nobody can quietly edit. Dashboards show estimates, but we give you evidence your CFO and your auditor can both trust.
4. **We plan to get paid only when you save.** The planned pricing is a cut of the verified savings, so if WattNot saves nothing, it costs nothing. Our incentive is the same as the customer's, and the receipts are the invoice.

## Why the EU report and compliance matter

Compliance is what turns this from a nice-to-have into a budget line.

Reporting is now mandatory in the EU for data centers of 500 kW or more, and investors and customers everywhere are asking for the same kind of numbers. Right now that reporting is expensive, manual and mostly estimated. WattNot's receipts are already the measured, signed evidence a report needs. So instead of a separate compliance project, the report comes free with the savings, generated in one click from numbers anyone can verify.

That gives an operator two reasons to say yes. It saves money on power and water, and it removes a reporting headache they'd have to pay for anyway. A full CSRD add-on is on our roadmap.

## Impact on AI infrastructure

Impact is why we built this.

When power is the bottleneck, efficiency *is* capacity. A 12.9% cut in energy per answer means roughly 15% more answers from the same power budget. That needs no new GPUs, no new substation, no years-long wait for the grid, and less cooling water.

Spread that across a fleet and the numbers get big. Operators can serve more users on the hardware they already have, push back expensive expansions, and lower the cost of every answer. Cheaper inference makes AI more affordable for everyone downstream, including startups, schools, hospitals and developers in countries where power is expensive or unreliable. That last point matters to us. We built this from Bengaluru, where grid power and water are both real constraints.

It also builds trust. The AI industry is under growing pressure over its energy and water footprint, and most of the claims being made are hard to check. WattNot puts a measured, signed number behind each claim. That's how this industry earns the right to keep growing.

Our long-term goal is simple. AI should be able to scale without its grid and water bill scaling right alongside it.

## Technical depth: how it's built

WattNot is a full system, not a wrapper around a model call.

- **Gateway.** An OpenAI-compatible proxy sits in front of vLLM. It routes live traffic, logs every request and response, and lets WattNot test changes on real traffic without touching client code. It also works with LangChain-style clients through the same API.
- **Telemetry.** NVIDIA NVML reads per-GPU power, cap, utilization, memory and temperature. Energy is measured by integrating power over each request window, so GPU energy on our H100 and Modal runs is MEASURED. On Baseten there is no GPU meter, so energy there is an ESTIMATE from tokens.
- **Levers.** These are the GPU power cap, vLLM batch size and max sequences, KV cache settings, prefix caching and FP8 quantization. The agent changes one lever per trial so every effect is easy to attribute.
- **The quality gate.** Each trial runs a fixed, held-out prompt set from public datasets. Answers are scored for correctness, and p99 latency has a hard limit. A Welch t-test checks that the energy difference is real. A sealed holdout set that the agent never sees during tuning guards against overfitting to the test.
- **Decision rule.** The AI proposes and explains, but a fixed, deterministic rule decides. Nothing ships unless it passes every gate.
- **Receipts.** Each kept change produces a JSON receipt with inputs, outputs, gate results and hardware details. Receipts are hash-chained so you can't delete or reorder them, and signed with Ed25519. `wattnot verify` recomputes the chain and checks the signatures. Receipts are signed offline; they are not published to any public transparency log.
- **Planner and frontier.** The agent tracks the energy-versus-quality frontier and picks the next trial most likely to move it, instead of brute-forcing every combination.
- **Multimodal checks.** Synthetic test images (pre-generated with Muse Image, labelled SYNTHETIC) exercise image workloads, voice requests exercise speech, and room photos feed rack segmentation, so the system isn't limited to text.
- **Replays.** Every run's event log is saved, so the live graph can replay any run step by step, offline, with no keys.
- **Runs on real hardware and the cloud.** We ran it on a dedicated H100, on Modal for GPU jobs, and on Baseten for hosted models, with receipts from each.
- **Engineered to be trusted.** There's a full test suite, labelled numbers everywhere, side branches for every change, and a public repo where everything was built during the hackathon, Oct 26 to Nov 4.

## Use of Meta's models

WattNot runs on Meta from end to end, and each of the 8 Meta tools has a job. Muse Glimmer 30B, which is open weights and self-hosted on our H100, is the model we serve and tune. Muse Spark Open, the second open-weights model, does effort routing on Baseten (energy there is an ESTIMATE, since there is no GPU meter). Muse Spark is the operator, and Muse Wonder, SAM 3.1, Muse Code and Muse Voice each do a specific job at runtime. Muse Image was used ahead of time to make the synthetic test images; no run calls it live. The full breakdown, and why open weights make WattNot possible, is in the README sections "Meta models used" and "Open weights: how, where, why".

## Business model

We take a percentage of the verified savings on the power bill, priced off the signed receipts. Test mode is the free or low-cost way in. Teams try it on their own prompts, see the receipt, and move to Plugin mode once they trust it. The EU report is included, and the CSRD add-on will be a paid upgrade. Our first customers are GPU cloud providers, AI-heavy companies running their own inference, and colocation data centers that need reporting for EU clients.

## Try it

The repo is public with install-and-run steps up top. Judges can replay every recorded run with no keys, or run it live using their own key (a capped shared demo key is on the roadmap, not built yet). A rules checklist in the README shows how each hackathon requirement is met, with a link to the proof.

WattNot makes AI cheaper to run, cleaner to run, and provably so, one verified saving at a time.
