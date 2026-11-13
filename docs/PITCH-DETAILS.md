# WattNot: pitch details

This is the longer version of the WattNot pitch deck for Startup Buildathon India 2026. The deck keeps each slide short, and each slide links to its section here. You don't need to know anything about GPUs or AI serving to read it.

- Demo video (2:47): https://youtu.be/toFEAICRTO8
- Code: https://github.com/ak9381/wattnot
- The guard repo is private and still a work in progress. Access on request.

Every part of WattNot carries one of three labels:
- **BUILT**: working code in the MVP today.
- **MEASURED**: we ran it on free GPUs, as scripts.
- **PLANNED**: we'll build it in the city round (Nov 20–21).

## A few words first

- **GPU time.** AI models run on GPUs, which are expensive chips that teams rent by the hour. GPU time is what they pay for.
- **Route.** One kind of request sent to one model. All the support chats sent to one model are one route, and all the code reviews sent to another model are another. WattNot handles each route on its own, because a change that's safe for one kind of request might not be safe for another.
- **Thinking tokens.** Many newer models write hidden working-out before they give their answer. That working-out is made of thinking tokens, and each one costs GPU time.
- **Rollback.** Undoing a change automatically when a check fails.
- **Receipt.** A signed record of a change and its result. Anyone can check it later, and if someone edits a number, the check fails.

## The problem

Teams that run open AI models on their own GPUs pay for every second of GPU time, and they've got three problems.

1. They can't see where the GPU time goes. Their dashboards show one total. They don't show, route by route, how much time goes to reading the prompt, to the model's thinking, or to writing the answer.
2. Any faster setting can change the answers. In our own test on one H100 GPU, switching to FP8 (a faster, less precise way of storing the model's numbers) cut energy 45%. It also changed 10 of 16 answers.
3. There's no fair test for "same answers". Run a model twice on the same question and it doesn't always agree with itself. A fixed pass mark ends up either too strict or too loose.

So most teams leave the default settings alone and buy more GPUs.

## Who is hurt

We got these pains from public job posts, public issues on vLLM (popular open-source software for serving models) and banks' self-hosting guides. We don't have customers yet.

- **Platform teams** pay per GPU-hour. They can't see the waste, so they buy more GPUs.
- **Teams on fixed GPU budgets.** This includes Indian banks and NBFCs (non-bank lenders) that keep models in India to follow RBI data rules, and builders on Indian GPU clouds.
- **End users.** When someone changes settings blind, users get empty answers and broken tool calls.

## How it works

WattNot learns from the traffic flowing through your app and frees GPU capacity, without changing answers. It works in three steps, one route at a time.

1. **Find (PLANNED).** WattNot Scope shows where each route's GPU time goes: reading the prompt, thinking, and writing the answer. Then it picks one change for the biggest waste. For example, a route that spends most of its time thinking gets a limit on thinking tokens. Scope can also estimate heat per layer of the model, but that's only an estimate, and we label it so.
2. **Try (BUILT).** We test the change on real traffic, replayed. Gates check quality and speed (the slowest 1% of replies). If a gate fails, the change rolls back.
3. **Prove (MEASURED).** We keep the change only if the bound says the answers held. See [The bound](#the-bound).

The GPU time we free becomes capacity: more requests on the GPUs you already pay for. Every keep or rollback gets a signed receipt.

### The parts

- **WattNot gateway (BUILT).** It sits between your apps and the model, and it works with apps that already use the OpenAI API format. It sorts traffic into routes by header, metadata or a hashed API key. A prompt fingerprint can only suggest a route. The **guard** catches empty answers and cut-off tool calls.
- **Trial engine and gates (BUILT).** It replays real traffic, runs the checks and keeps or rolls back.
- **Signed receipts (BUILT).** Each one is signed with Ed25519 and chained to the one before it, like pages in a numbered, sealed logbook. They hold no prompt text.
- **Scope and the step that picks one change (PLANNED).**
- **The bound (MEASURED, as scripts).** The certify CLI, the command-line tool that would run it for you, isn't built.

## The bound

After a change, we want to know one thing: how many answers changed, and is that within what we allow?

- **The bound (U95).** We count how many answers changed on a set of prompts, then work out the most that could really have changed, at 95% confidence. It's a cautious upper limit, so a small test can't make a change look safer than it is. (The maths is the Clopper–Pearson interval.)
- **The noise floor (d₀).** Run the original model twice on the same prompts and some answers will still differ. That natural wobble is d₀. It isn't fair to blame a change for differences the model makes on its own.
- **The budget (α).** The extra changed answers you're willing to allow, for example 5%.

The rule: keep a change only if **U95 ≤ d₀ + α**.

To keep it honest, we split the prompts in two. We pick the setting on one half, and we measure the bound on the other half, which the picking step never saw.

**What's measured so far:** d₀ isn't measured yet. In week 3 we compared each answer with the full-length answer from the same run, using a fixed 5% budget. The d₀ version is next.

## Evidence

**MEASURED in week 3, as scripts.** We used free Kaggle T4 GPUs and a 5% budget. The change we tested was a limit on thinking tokens for each route. We picked the limit on one half of the prompts and scored it on the other half.

| Route (question set · model) | Thinking tokens cut | Answers changed | Bound (U95) |
|---|---|---|---|
| GSM8K (school maths) · R1-Distill-1.5B | 16.4% | 25 of 649 | 5.3%, just over the 5% target |
| GSM8K · R1-Distill-7B | 16.5% | 10 of 404 | 4.2% |
| MATH-500 (harder maths) | 0% | 0 | left unchanged |
| SuperGPQA (hard graduate-level questions) | 0% | 0 | left unchanged |

**Why 0% on the harder sets.** On MATH-500 and SuperGPQA, every shorter thinking limit changed too many answers on the picking half to stay inside the 5% budget. So WattNot chose the full-length limit (the same 4,096 tokens the models already had), which means no change at all. That's the safety check doing its job: when it can't prove a cut is safe, it doesn't make one.

At a stricter 1–2% budget, the cut was about 0% on every route.

**Prior work (the plumbing).** Before this, we built the tuning loop for a different hackathon, on Muse Glimmer 30B on one H100, not Gemma:
- 12.9% less energy per answer on live traffic (54/60 both arms, p = 0.027).
- 26.7% less on a sealed set of 977 prompts.
- The loop rolled FP8 back by itself, because FP8 changed 10 of 16 answers.

## What is new

We don't claim the tuning loop, the gates, power caps, batching or FP8 as new. They're plumbing.

What's new:
1. **Scope as a money tool.** What it produces is freed capacity. We score it against trying settings blind: how many trials and GPU-hours each takes to reach the same saving.
2. **A bound on changed answers for each route, set against the model's own wobble (d₀).** The closest research doesn't handle a model that disagrees with itself, and it lists live checking as future work.

What today's tools don't do:
- Power-capping tools tune the hardware for speed, not for answers.
- Model routers promise "no degradation" without a bound you can check.
- Research methods bound changes against a fixed reference, and stop at the paper.

## MVP built vs planned

**Built (a working MVP, still a work in progress):**
- A gateway that sorts traffic by route.
- Gated trials with automatic rollback.
- Signed receipts (Ed25519, chained).
- The guard, which catches empty answers and cut-off tool calls.

**Measured, as scripts:** the week-3 bound runs. See [Evidence](#evidence).

**Planned for the city round:**
- Scope, and the test against blind trial and error.
- The certify CLI.
- Measuring d₀.
- Gemma on vLLM on Google Cloud.
- Gemini as grader, test writer and planner.

We rebuilt this for Google AI from our earlier WattNot MVP (Oct–Nov 2026, 891 commits).

## Google AI tools and models

Google runs through every step of WattNot. All of the parts below are PLANNED for the city round. Neither model is in the built version yet.

### At each step

- **Find.** Gemma is the model we tune. Because its weights are open, Scope can look inside it and time each step. The Gemini API reads what Scope finds and suggests one change.
- **Try.** Gemma runs on vLLM on a Google Cloud GPU VM (Compute Engine or GKE). We need our own GPU machine because some settings, like power caps, need full control of it. Cloud Run runs the trial engine and the dashboard.
- **Prove.** The Gemini API grades whether free-text answers changed, where there's no exact answer to compare against. That's what lets us put a bound on chat-style routes, not only maths. Cloud Storage or Firestore keeps the signed receipts, which are append-only and easy to check.

### Why Gemma and Gemini

- **Gemma is the model being tuned.** Open weights mean we can run it ourselves, change how it's served and time each step. A closed API won't let you do any of that. It also lets customers keep their data in their own cloud, in India.
- **Gemini is the checker.** It's a separate model from the one being tuned, so the model we're speeding up never marks its own homework. It also writes edge-case test prompts for each route. It advises, and fixed code makes every keep or roll-back call.

### How we'll build it

- **Google AI Studio:** the Gemini API key, and testing the grader prompt on questions with known answers before it's allowed to gate anything.
- **Google Antigravity:** porting the built gateway, gates and receipts to Gemma, and building Scope's timing. Akash reviews every change.

### Already used

- **Gemini Deep Research** for our customer research, which we then fact-checked.

### City-round plan

- **Day 1:** Gemma 3 on vLLM. Scope's split on one real route. One gated trial with a signed receipt.
- **Day 2:** Scope against blind trial and error, counted in trials and GPU-hours. The bound on a Gemma route. The dashboard on Cloud Run.

### Later (an idea, not built)

The same bound for hosted API spend, such as Gemini's own thinking levels.

## Future scope

**Not built, not measured.** The links between GPUs are moving from copper to optics. Faster links only free capacity if your traffic actually uses them. Scope would add a "communication" slice and show, route by route, whether it does. Then it could suggest one of three changes: split the model across GPUs differently, send bigger transfers, or use fewer GPUs.

## Why now

98% of FinOps teams (the people who manage cloud bills) now manage AI spend too, according to [State of FinOps 2026](https://data.finops.org/).

## Business model

This is a test, not proven. We have no customers or revenue yet.

1. **Free:** the guard. It brings platform engineers to us.
2. **Capacity audit, $300 (a test price):** Scope and the bound run on the customer's own traffic and GPUs. They pay only after they reproduce a saving of at least 15% themselves.
3. **Subscription after the audit:** re-checking the bound when models or traffic change, new trials and signed reports.

## Sources

- Week-3 bound runs: our own logs (thinking-limit data, the 5% budget rows).
- Prior work: our earlier WattNot build on one H100 (Oct–Nov 2026).
- State of FinOps 2026: https://data.finops.org/ and the [FinOps Foundation press release](https://www.linuxfoundation.org/press/state-of-finops-survey-ai-value-and-skills-top-priorities-as-finops-matures-across-technology-value-98-manage-ai-90-saas-64-licensing-48-data-center-1).
