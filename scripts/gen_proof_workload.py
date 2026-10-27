#!/usr/bin/env python3
"""Build workloads/proof-v1.jsonl (+ the gold answers used to test the gate).

Three kinds of task, each with an answer a program can check:
  math  50 GSM8K test problems (MIT licence, github.com/openai/gsm8k) with at most 3 steps
  code  29 small Python functions, each with unit tests
  qa    32 short factual questions with accepted spellings

Run once, commit the output. The math rows need the GSM8K test parquet:
  python scripts/gen_proof_workload.py --gsm8k path/to/test-00000-of-00001.parquet
"""
from __future__ import annotations

import argparse
import json
import random
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

MATH_SUFFIX = ("\n\nWork it out step by step. End with one last line in exactly this form: "
               "#### <number>")
CODE_SUFFIX = "\n\nReply with the function only, in one ```python code block. No explanation."
QA_SUFFIX = "\n\nAnswer with just the answer, a few words at most."

# (entry point, description, reference solution, test lines)
CODE = [
    ("add", "def add(a, b): return the sum of two numbers.", "def add(a, b):\n    return a + b",
     ["assert add(2, 3) == 5", "assert add(-1, 1) == 0", "assert add(0.5, 0.25) == 0.75"]),
    ("is_even", "def is_even(n): return True when the integer n is even.",
     "def is_even(n):\n    return n % 2 == 0",
     ["assert is_even(4) is True", "assert is_even(7) is False", "assert is_even(0) is True",
      "assert is_even(-2) is True"]),
    ("reverse_string", "def reverse_string(s): return the string s reversed.",
     "def reverse_string(s):\n    return s[::-1]",
     ["assert reverse_string('abc') == 'cba'", "assert reverse_string('') == ''"]),
    ("count_vowels", "def count_vowels(s): count the vowels (a, e, i, o, u, either case) in s.",
     "def count_vowels(s):\n    return sum(c in 'aeiouAEIOU' for c in s)",
     ["assert count_vowels('hello') == 2", "assert count_vowels('RHYTHM') == 0",
      "assert count_vowels('AEIOUaeiou') == 10"]),
    ("factorial", "def factorial(n): return n! for an integer n >= 0 (factorial(0) is 1).",
     "def factorial(n):\n    r = 1\n    for i in range(2, n + 1):\n        r *= i\n    return r",
     ["assert factorial(0) == 1", "assert factorial(5) == 120", "assert factorial(10) == 3628800"]),
    ("fibonacci", "def fibonacci(n): return the n-th Fibonacci number, with fibonacci(0) == 0 "
     "and fibonacci(1) == 1.",
     "def fibonacci(n):\n    a, b = 0, 1\n    for _ in range(n):\n        a, b = b, a + b\n    return a",
     ["assert fibonacci(0) == 0", "assert fibonacci(1) == 1", "assert fibonacci(10) == 55",
      "assert fibonacci(20) == 6765"]),
    ("is_prime", "def is_prime(n): return True when the integer n is prime.",
     "def is_prime(n):\n    if n < 2:\n        return False\n    i = 2\n    while i * i <= n:\n"
     "        if n % i == 0:\n            return False\n        i += 1\n    return True",
     ["assert is_prime(2) is True", "assert is_prime(1) is False", "assert is_prime(0) is False",
      "assert is_prime(97) is True", "assert is_prime(91) is False"]),
    ("max_of_list", "def max_of_list(xs): return the largest number in the non-empty list xs, "
     "without using max().",
     "def max_of_list(xs):\n    best = xs[0]\n    for x in xs[1:]:\n        if x > best:\n"
     "            best = x\n    return best",
     ["assert max_of_list([3, 9, 2]) == 9", "assert max_of_list([-5, -2, -9]) == -2",
      "assert max_of_list([7]) == 7"]),
    ("is_palindrome", "def is_palindrome(s): True when s reads the same backwards, ignoring case "
     "and any character that is not a letter or digit.",
     "def is_palindrome(s):\n    t = [c.lower() for c in s if c.isalnum()]\n    return t == t[::-1]",
     ["assert is_palindrome('A man, a plan, a canal: Panama') is True",
      "assert is_palindrome('hello') is False", "assert is_palindrome('') is True"]),
    ("sum_digits", "def sum_digits(n): return the sum of the decimal digits of the integer n "
     "(use abs(n) for negatives).",
     "def sum_digits(n):\n    return sum(int(c) for c in str(abs(n)))",
     ["assert sum_digits(123) == 6", "assert sum_digits(-45) == 9", "assert sum_digits(0) == 0"]),
    ("flatten", "def flatten(xs): flatten a list of lists (one level deep) into one list, "
     "keeping order.",
     "def flatten(xs):\n    return [y for x in xs for y in x]",
     ["assert flatten([[1, 2], [3], []]) == [1, 2, 3]", "assert flatten([]) == []"]),
    ("unique_sorted", "def unique_sorted(xs): return the distinct values of xs as a sorted list.",
     "def unique_sorted(xs):\n    return sorted(set(xs))",
     ["assert unique_sorted([3, 1, 3, 2, 1]) == [1, 2, 3]", "assert unique_sorted([]) == []"]),
    ("word_count", "def word_count(s): return a dict mapping each lowercase word in s "
     "(split on whitespace) to how many times it appears.",
     "def word_count(s):\n    d = {}\n    for w in s.lower().split():\n        d[w] = d.get(w, 0) + 1\n"
     "    return d",
     ["assert word_count('a b a') == {'a': 2, 'b': 1}", "assert word_count('') == {}",
      "assert word_count('Go go GO') == {'go': 3}"]),
    ("gcd", "def gcd(a, b): return the greatest common divisor of two positive integers.",
     "def gcd(a, b):\n    while b:\n        a, b = b, a % b\n    return a",
     ["assert gcd(12, 18) == 6", "assert gcd(17, 5) == 1", "assert gcd(100, 25) == 25"]),
    ("lcm", "def lcm(a, b): return the least common multiple of two positive integers.",
     "def lcm(a, b):\n    x, y = a, b\n    while y:\n        x, y = y, x % y\n    return a * b // x",
     ["assert lcm(4, 6) == 12", "assert lcm(7, 3) == 21", "assert lcm(5, 5) == 5"]),
    ("fizzbuzz", "def fizzbuzz(n): return the list of strings for 1..n where multiples of 3 are "
     "'Fizz', multiples of 5 are 'Buzz', multiples of both are 'FizzBuzz', otherwise the number "
     "as a string.",
     "def fizzbuzz(n):\n    out = []\n    for i in range(1, n + 1):\n        if i % 15 == 0:\n"
     "            out.append('FizzBuzz')\n        elif i % 3 == 0:\n            out.append('Fizz')\n"
     "        elif i % 5 == 0:\n            out.append('Buzz')\n        else:\n"
     "            out.append(str(i))\n    return out",
     ["assert fizzbuzz(5) == ['1', '2', 'Fizz', '4', 'Buzz']", "assert fizzbuzz(15)[-1] == 'FizzBuzz'",
      "assert fizzbuzz(0) == []"]),
    ("celsius_to_f", "def celsius_to_f(c): convert Celsius to Fahrenheit.",
     "def celsius_to_f(c):\n    return c * 9 / 5 + 32",
     ["assert celsius_to_f(0) == 32", "assert celsius_to_f(100) == 212",
      "assert abs(celsius_to_f(-40) - (-40)) < 1e-9"]),
    ("second_largest", "def second_largest(xs): return the second largest distinct value in xs, "
     "or None when there are fewer than two distinct values.",
     "def second_largest(xs):\n    u = sorted(set(xs))\n    return u[-2] if len(u) >= 2 else None",
     ["assert second_largest([1, 5, 3]) == 3", "assert second_largest([4, 4]) is None",
      "assert second_largest([9, 9, 8]) == 8"]),
    ("char_freq_top", "def char_freq_top(s): return the most common character in the non-empty "
     "string s; on a tie return the one that appears first in s.",
     "def char_freq_top(s):\n    best, n = s[0], 0\n    for c in s:\n        k = s.count(c)\n"
     "        if k > n:\n            best, n = c, k\n    return best",
     ["assert char_freq_top('abbc') == 'b'", "assert char_freq_top('abab') == 'a'",
      "assert char_freq_top('z') == 'z'"]),
    ("binary_search", "def binary_search(xs, target): xs is a sorted list; return the index of "
     "target, or -1 when it is not there.",
     "def binary_search(xs, target):\n    lo, hi = 0, len(xs) - 1\n    while lo <= hi:\n"
     "        mid = (lo + hi) // 2\n        if xs[mid] == target:\n            return mid\n"
     "        if xs[mid] < target:\n            lo = mid + 1\n        else:\n            hi = mid - 1\n"
     "    return -1",
     ["assert binary_search([1, 3, 5, 7], 5) == 2", "assert binary_search([1, 3, 5, 7], 4) == -1",
      "assert binary_search([], 1) == -1"]),
    ("merge_sorted", "def merge_sorted(a, b): merge two sorted lists into one sorted list.",
     "def merge_sorted(a, b):\n    i = j = 0\n    out = []\n    while i < len(a) and j < len(b):\n"
     "        if a[i] <= b[j]:\n            out.append(a[i]); i += 1\n        else:\n"
     "            out.append(b[j]); j += 1\n    return out + a[i:] + b[j:]",
     ["assert merge_sorted([1, 4], [2, 3]) == [1, 2, 3, 4]", "assert merge_sorted([], [1]) == [1]",
      "assert merge_sorted([1, 1], [1]) == [1, 1, 1]"]),
    ("transpose", "def transpose(m): transpose a non-empty rectangular matrix given as a list of "
     "row lists.",
     "def transpose(m):\n    return [list(r) for r in zip(*m)]",
     ["assert transpose([[1, 2, 3], [4, 5, 6]]) == [[1, 4], [2, 5], [3, 6]]",
      "assert transpose([[1]]) == [[1]]"]),
    ("to_roman", "def to_roman(n): convert an integer from 1 to 3999 to a Roman numeral.",
     "def to_roman(n):\n    vals = [(1000, 'M'), (900, 'CM'), (500, 'D'), (400, 'CD'), (100, 'C'), "
     "(90, 'XC'), (50, 'L'), (40, 'XL'), (10, 'X'), (9, 'IX'), (5, 'V'), (4, 'IV'), (1, 'I')]\n"
     "    out = ''\n    for v, s in vals:\n        while n >= v:\n            out += s\n            n -= v\n"
     "    return out",
     ["assert to_roman(4) == 'IV'", "assert to_roman(1994) == 'MCMXCIV'",
      "assert to_roman(3999) == 'MMMCMXCIX'"]),
    ("is_anagram", "def is_anagram(a, b): True when a and b use the same letters the same number "
     "of times, ignoring case and spaces.",
     "def is_anagram(a, b):\n    f = lambda s: sorted(c.lower() for c in s if not c.isspace())\n"
     "    return f(a) == f(b)",
     ["assert is_anagram('Listen', 'Silent') is True", "assert is_anagram('abc', 'abd') is False",
      "assert is_anagram('a b', 'ba') is True"]),
    ("balanced_brackets", "def balanced_brackets(s): True when every (, [ and { in s is closed in "
     "the right order. Other characters are ignored.",
     "def balanced_brackets(s):\n    pairs = {')': '(', ']': '[', '}': '{'}\n    st = []\n"
     "    for c in s:\n        if c in '([{':\n            st.append(c)\n        elif c in pairs:\n"
     "            if not st or st.pop() != pairs[c]:\n                return False\n"
     "    return not st",
     ["assert balanced_brackets('([]{})') is True", "assert balanced_brackets('(]') is False",
      "assert balanced_brackets('((') is False", "assert balanced_brackets('') is True"]),
    ("running_sum", "def running_sum(xs): return the list of running totals of xs.",
     "def running_sum(xs):\n    out, t = [], 0\n    for x in xs:\n        t += x\n        out.append(t)\n"
     "    return out",
     ["assert running_sum([1, 2, 3]) == [1, 3, 6]", "assert running_sum([]) == []"]),
    ("rotate_left", "def rotate_left(xs, k): return xs rotated left by k places (k may be larger "
     "than len(xs); an empty list stays empty).",
     "def rotate_left(xs, k):\n    if not xs:\n        return []\n    k %= len(xs)\n"
     "    return xs[k:] + xs[:k]",
     ["assert rotate_left([1, 2, 3, 4], 1) == [2, 3, 4, 1]", "assert rotate_left([1, 2, 3], 5) == "
      "[3, 1, 2]", "assert rotate_left([], 3) == []"]),
    ("title_case", "def title_case(s): capitalise the first letter of each space-separated word "
     "and lowercase the rest.",
     "def title_case(s):\n    return ' '.join(w[:1].upper() + w[1:].lower() for w in s.split(' '))",
     ["assert title_case('hello wORLD') == 'Hello World'", "assert title_case('a') == 'A'"]),
    ("count_pairs", "def count_pairs(xs, target): count the pairs i < j with xs[i] + xs[j] == "
     "target.",
     "def count_pairs(xs, target):\n    n = 0\n    for i in range(len(xs)):\n"
     "        for j in range(i + 1, len(xs)):\n            if xs[i] + xs[j] == target:\n"
     "                n += 1\n    return n",
     ["assert count_pairs([1, 2, 3, 4], 5) == 2", "assert count_pairs([2, 2, 2], 4) == 3",
      "assert count_pairs([], 1) == 0"]),
]

# (question, accepted answers). The first answer is the reference.
QA = [
    ("What is the capital of France?", ["Paris"]),
    ("What is the capital of Japan?", ["Tokyo"]),
    ("What is the capital of Australia?", ["Canberra"]),
    ("What is the capital of Canada?", ["Ottawa"]),
    ("What is the capital of Egypt?", ["Cairo"]),
    ("What is the capital of Brazil?", ["Brasilia", "Brasília"]),
    ("What is the chemical symbol for gold?", ["Au"]),
    ("What is the chemical symbol for sodium?", ["Na"]),
    ("What is the chemical formula for water?", ["H2O", "H₂O"]),
    ("Which planet is closest to the Sun?", ["Mercury"]),
    ("Which planet is known as the Red Planet?", ["Mars"]),
    ("What is the largest planet in the Solar System?", ["Jupiter"]),
    ("Who wrote the play Romeo and Juliet?", ["William Shakespeare", "Shakespeare"]),
    ("Who painted the Mona Lisa?", ["Leonardo da Vinci", "da Vinci", "Leonardo"]),
    ("Who developed the theory of general relativity?", ["Albert Einstein", "Einstein"]),
    ("In which year did World War II end?", ["1945"]),
    ("In which year did humans first land on the Moon?", ["1969"]),
    ("How many continents are there on Earth?", ["7", "seven"]),
    ("How many sides does a hexagon have?", ["6", "six"]),
    ("What is the square root of 144?", ["12", "twelve"]),
    ("What is the largest ocean on Earth?", ["Pacific Ocean", "Pacific"]),
    ("What is the longest river in Africa?", ["Nile", "River Nile", "Nile River"]),
    ("What is the tallest mountain above sea level on Earth?", ["Mount Everest", "Everest"]),
    ("What gas do plants absorb from the air for photosynthesis?",
     ["Carbon dioxide", "CO2", "CO₂"]),
    ("What is the hardest natural substance?", ["Diamond"]),
    ("What is the currency of Japan?", ["Yen", "Japanese yen"]),
    ("Which language has the most native speakers in the world?",
     ["Mandarin Chinese", "Mandarin", "Chinese"]),
    ("What is the freezing point of water in degrees Celsius?", ["0", "zero"]),
    ("Who was the first person to walk on the Moon?", ["Neil Armstrong", "Armstrong"]),
    ("What is the smallest prime number?", ["2", "two"]),
    ("What is the capital of Italy?", ["Rome"]),
    ("What is the chemical symbol for iron?", ["Fe"]),
]


def gsm8k_rows(parquet: Path, n: int = 50, seed: int = 7) -> list[dict]:
    import pyarrow.parquet as pq

    rows = pq.read_table(parquet).to_pylist()
    ok = []
    for i, r in enumerate(rows):
        steps = len(re.findall(r"<<", r["answer"]))
        final = r["answer"].split("####")[-1].strip().replace(",", "")
        if steps <= 3 and re.fullmatch(r"-?\d+(\.\d+)?", final):
            ok.append((i, r))
    random.Random(seed).shuffle(ok)
    return [{"idx": i, **r} for i, r in sorted(ok[:n], key=lambda t: t[0])]


def clean_solution(answer: str) -> str:
    body, final = answer.split("####")
    body = re.sub(r"<<[^>]*>>", "", body).strip()
    return f"{body}\n#### {final.strip().replace(',', '')}"


def build(parquet: Path) -> tuple[list[dict], list[dict]]:
    work, gold = [], []
    for r in gsm8k_rows(parquet):
        wid = f"math-{r['idx']:04d}"
        ans = r["answer"].split("####")[-1].strip().replace(",", "")
        work.append({"id": wid, "kind": "math", "source": f"gsm8k-test-{r['idx']}",
                     "prompt": r["question"] + MATH_SUFFIX, "max_tokens": 512,
                     "checks": [{"type": "math", "value": ans}]})
        gold.append({"id": wid, "output": clean_solution(r["answer"])})
    for name, desc, ref, tests in CODE:
        wid = f"code-{name}"
        work.append({"id": wid, "kind": "code", "source": "wattnot-original",
                     "prompt": "Write a Python function.\n" + desc + CODE_SUFFIX, "max_tokens": 400,
                     "checks": [{"type": "code", "value": name, "tests": tests}]})
        gold.append({"id": wid, "output": f"```python\n{ref}\n```"})
    for k, (q, answers) in enumerate(QA):
        wid = f"qa-{k:02d}"
        work.append({"id": wid, "kind": "qa", "source": "wattnot-original",
                     "prompt": q + QA_SUFFIX, "max_tokens": 32,
                     "checks": [{"type": "qa", "value": answers[0], "aliases": answers}]})
        gold.append({"id": wid, "output": answers[0]})
    return work, gold


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gsm8k", required=True, type=Path)
    a = ap.parse_args()
    work, gold = build(a.gsm8k)
    d = ROOT / "workloads"
    with (d / "proof-v1.jsonl").open("w") as f:
        for w in work:
            f.write(json.dumps(w, ensure_ascii=False) + "\n")
    with (d / "proof-v1.gold.jsonl").open("w") as f:
        for g in gold:
            f.write(json.dumps(g, ensure_ascii=False) + "\n")
    print(len(work), "items")


if __name__ == "__main__":
    main()
