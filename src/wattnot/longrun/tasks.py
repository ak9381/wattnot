"""The tiered prompt set: Easy / Medium / Hard / Complex (tiers-v1).

All items are written here, benchmark-style (arithmetic, GSM-style word problems, HumanEval-style
function tasks, tool-using agent tasks). Nothing comes from any private or customer data set.
Generation is deterministic (fixed seed), so the stock and capped arms always see the same items.

- easy     one-step arithmetic / unit facts                    grader: exact number
- medium   2-3 step word problems                               grader: final number
- hard     write a Python function                               grader: run hidden tests
- complex  a multi-step agent task in a small tool world:        grader: final number AND the
           tool calls (inventory, price, fx, calc), a flaky              required tools were used
           price tool that fails the first call (retry), and             (and the sub-agent, when
           a `subagent` tool for delegated sub-tasks                     the task asks for one)
"""

from __future__ import annotations

import json
import random
from dataclasses import asdict, dataclass, field
from pathlib import Path

TIERS = ("easy", "medium", "hard", "complex")
SEED = 20261029


@dataclass
class Task:
    id: str
    tier: str
    prompt: str
    answer: str | float | None = None      # easy/medium/complex: the number
    tests: list[str] = field(default_factory=list)  # hard: assert lines
    entry: str | None = None                # hard: function name
    max_tokens: int = 64
    needs_tools: list[str] = field(default_factory=list)  # complex: tools that must be called
    needs_subagent: bool = False
    world: dict | None = None               # complex: the tool world for this task

    def as_dict(self) -> dict:
        return asdict(self)


NUM_SUFFIX = " Reply with only the final number."


def _easy(r: random.Random) -> list[Task]:
    out = []
    for i in range(30):
        k = i % 5
        a, b = r.randint(12, 99), r.randint(3, 60)
        if k == 0:
            p, ans = f"What is {a} + {b}?", a + b
        elif k == 1:
            p, ans = f"What is {a} times {b % 12 + 2}?", a * (b % 12 + 2)
        elif k == 2:
            p, ans = f"What is {a * 4} divided by 4?", a
        elif k == 3:
            p, ans = f"How many minutes are in {b % 9 + 2} hours?", (b % 9 + 2) * 60
        else:
            p, ans = f"What is {a + 100} minus {b}?", a + 100 - b
        out.append(Task(f"easy-{i:02d}", "easy", p + NUM_SUFFIX, ans, max_tokens=16))
    return out


def _medium(r: random.Random) -> list[Task]:
    names = ["Asha", "Ben", "Chen", "Dana", "Eli", "Farah", "Gus", "Hana", "Ivo", "Jun"]
    items = ["notebooks", "apples", "bolts", "tickets", "candles", "stamps"]
    out = []
    for i in range(30):
        n, it = names[i % len(names)], items[i % len(items)]
        a, b, c = r.randint(3, 12), r.randint(2, 9), r.randint(5, 40)
        k = i % 3
        if k == 0:
            p = (f"{n} buys {a} packs of {it} with {b} in each pack, then gives away {c % (a * b)}"
                 f" {it}. How many {it} does {n} have left?")
            ans = a * b - c % (a * b)
        elif k == 1:
            price = r.randint(2, 9)
            p = (f"A shop sells {it} at ${price} each. {n} buys {a} on Monday and twice as many on"
                 f" Tuesday, and pays with ${(3 * a * price) + c}. How much change, in dollars?")
            ans = c
        else:
            p = (f"{n} reads {a} pages a day for {b} days, then {a + 3} pages a day for {b + 1} days."
                 f" How many pages in total?")
            ans = a * b + (a + 3) * (b + 1)
        out.append(Task(f"medium-{i:02d}", "medium",
                        p + " Think step by step, then end with 'ANSWER: <number>'.", ans,
                        max_tokens=256))
    return out


HARD = [
    ("is_palindrome", "is_palindrome(s: str) -> bool: True if s reads the same backwards, "
     "ignoring case and non-alphanumeric characters",
     ["is_palindrome('A man, a plan, a canal: Panama') is True", "is_palindrome('abc') is False",
      "is_palindrome('') is True"]),
    ("gcd", "gcd(a: int, b: int) -> int: greatest common divisor of two non-negative ints",
     ["gcd(12, 18) == 6", "gcd(7, 0) == 7", "gcd(17, 5) == 1"]),
    ("rle", "rle(s: str) -> str: run-length encode, e.g. 'aaab' -> 'a3b1'",
     ["rle('aaab') == 'a3b1'", "rle('') == ''", "rle('abcc') == 'a1b1c2'"]),
    ("flatten", "flatten(xs: list) -> list: flatten arbitrarily nested lists",
     ["flatten([1, [2, [3, [4]]]]) == [1, 2, 3, 4]", "flatten([]) == []"]),
    ("second_largest", "second_largest(xs: list[int]) -> int | None: the second largest "
     "distinct value, or None", ["second_largest([3, 1, 3, 2]) == 2",
                                 "second_largest([5]) is None"]),
    ("word_count", "word_count(s: str) -> dict[str, int]: lowercase word counts, words are "
     "runs of letters", ["word_count('Hi hi, there!') == {'hi': 2, 'there': 1}",
                         "word_count('') == {}"]),
    ("roman", "roman(n: int) -> str: Roman numeral for 1 <= n <= 3999",
     ["roman(1994) == 'MCMXCIV'", "roman(4) == 'IV'", "roman(3999) == 'MMMCMXCIX'"]),
    ("merge_intervals", "merge_intervals(xs: list[tuple[int, int]]) -> list[tuple[int, int]]: "
     "merge overlapping closed intervals, sorted", ["merge_intervals([(1, 3), (2, 6), (8, 10)]) "
                                                    "== [(1, 6), (8, 10)]",
                                                    "merge_intervals([]) == []"]),
    ("is_prime", "is_prime(n: int) -> bool", ["is_prime(2) and is_prime(97)",
                                              "not is_prime(1) and not is_prime(91)"]),
    ("binary_search", "binary_search(xs: list[int], t: int) -> int: index of t in sorted xs "
     "or -1", ["binary_search([1, 3, 5, 7], 5) == 2", "binary_search([1, 3], 2) == -1"]),
    ("anagrams", "anagrams(a: str, b: str) -> bool: same letters, ignoring case and spaces",
     ["anagrams('Listen', 'Silent')", "not anagrams('ab', 'abc')"]),
    ("fib", "fib(n: int) -> int: nth Fibonacci number, fib(0) = 0, fib(1) = 1",
     ["fib(10) == 55", "fib(0) == 0", "fib(50) == 12586269025"]),
    ("chunk", "chunk(xs: list, n: int) -> list[list]: split into chunks of size n",
     ["chunk([1, 2, 3, 4, 5], 2) == [[1, 2], [3, 4], [5]]", "chunk([], 3) == []"]),
    ("balanced", "balanced(s: str) -> bool: brackets ()[]{} are balanced",
     ["balanced('([]{})')", "not balanced('(]')", "balanced('')"]),
    ("dedupe", "dedupe(xs: list) -> list: remove duplicates, keep first-seen order",
     ["dedupe([3, 1, 3, 2, 1]) == [3, 1, 2]"]),
    ("camel_to_snake", "camel_to_snake(s: str) -> str: 'helloWorldX' -> 'hello_world_x'",
     ["camel_to_snake('helloWorldX') == 'hello_world_x'", "camel_to_snake('a') == 'a'"]),
    ("digits_sum", "digits_sum(n: int) -> int: sum of decimal digits of |n|",
     ["digits_sum(-4096) == 19", "digits_sum(0) == 0"]),
    ("transpose", "transpose(m: list[list[int]]) -> list[list[int]]",
     ["transpose([[1, 2, 3], [4, 5, 6]]) == [[1, 4], [2, 5], [3, 6]]"]),
    ("median", "median(xs: list[float]) -> float: median of a non-empty list",
     ["median([3, 1, 2]) == 2", "median([4, 1, 3, 2]) == 2.5"]),
    ("caesar", "caesar(s: str, k: int) -> str: shift letters by k, keep case and non-letters",
     ["caesar('Abc-z', 1) == 'Bcd-a'", "caesar('x', 29) == 'a'"]),
]


def _hard() -> list[Task]:
    return [Task(f"hard-{i:02d}", "hard",
                 f"Write a Python function `{spec}`. Reply with only the code in one ```python "
                 "block, no explanation.", tests=tests, entry=name, max_tokens=400)
            for i, (name, spec, tests) in enumerate(HARD)]


TOOLS_DOC = """You can call tools. To call one, reply with exactly one line:
CALL <tool> <json arguments>
and wait for the RESULT. Tools:
- inventory {"item": str} -> units in stock
- price {"item": str} -> unit price in USD (this service is flaky: if it errors, call it again)
- fx {"amount": number, "to": "EUR"|"GBP"|"INR"} -> converted amount
- calc {"expr": str} -> the value of an arithmetic expression
- subagent {"task": str} -> a helper agent does the task with the same tools and returns its answer
When you are done, reply with one line: FINAL <number>"""

FX = {"EUR": 0.92, "GBP": 0.79, "INR": 88.0}


def _complex(r: random.Random) -> list[Task]:
    catalog = ["gpu-fan", "psu-1600w", "nvlink-bridge", "ssd-4tb", "ram-64gb", "nic-100g"]
    out = []
    for i in range(12):
        a, b = r.sample(catalog, 2)
        world = {"inventory": {x: r.randint(5, 60) for x in catalog},
                 "price": {x: r.randint(20, 400) for x in catalog},
                 "fx": FX, "flaky": ["price"]}
        target = r.choice([80, 100, 120])
        need_a = max(0, target - world["inventory"][a])
        need_b = max(0, target - world["inventory"][b])
        usd = need_a * world["price"][a] + need_b * world["price"][b]
        k = i % 3
        if k == 0:
            cur = "EUR"
            q = (f"We must restock {a} and {b} to {target} units each. What is the total restock "
                 f"cost in {cur}, rounded to 2 decimals?")
            ans, tools, sub = round(usd * FX[cur], 2), ["inventory", "price", "fx"], False
        elif k == 1:
            q = (f"Use a subagent to find the restock cost in USD for {a} (to {target} units) and "
                 f"do {b} (to {target} units) yourself. Report the combined USD total.")
            ans, tools, sub = float(usd), ["inventory", "price"], True
        else:
            cur = "INR"
            q = (f"Which costs more to restock to {target} units, {a} or {b}? Report the larger "
                 f"restock cost in {cur}, rounded to 2 decimals.")
            ans = round(max(need_a * world["price"][a], need_b * world["price"][b]) * FX[cur], 2)
            tools, sub = ["inventory", "price", "fx"], False
        out.append(Task(f"complex-{i:02d}", "complex", TOOLS_DOC + "\n\nTask: " + q, ans,
                        max_tokens=192, needs_tools=tools, needs_subagent=sub, world=world))
    return out


def build(seed: int = SEED) -> list[Task]:
    r = random.Random(seed)
    return _easy(r) + _medium(r) + _hard() + _complex(r)


def write_jsonl(path: Path, tasks: list[Task] | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        for t in tasks or build():
            f.write(json.dumps(t.as_dict(), ensure_ascii=False) + "\n")
    return path


def samples_for_ui(n_per_tier: int = 2, tasks: list[Task] | None = None) -> dict:
    """A small, display-ready sample per tier for a dashboard panel (no answers, no tests)."""
    tasks = tasks or build()
    desc = {"easy": "one-step arithmetic", "medium": "multi-step word problems",
            "hard": "write a Python function; graded by running hidden tests",
            "complex": "multi-step agent task: tool calls, a flaky tool that needs a retry, "
                       "sub-agents; graded on the answer and the tools used"}
    out = {"version": "tiers-v1", "label": "workload sample", "tiers": []}
    for tier in TIERS:
        ts = [t for t in tasks if t.tier == tier]
        out["tiers"].append({
            "tier": tier, "count": len(ts), "what": desc[tier],
            "samples": [{"id": t.id, "prompt": t.prompt.split("\n\nTask: ")[-1][:400],
                         "tools": t.needs_tools, "subagent": t.needs_subagent}
                        for t in ts[:n_per_tier]]})
    return out
