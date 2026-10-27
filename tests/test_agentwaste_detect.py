from __future__ import annotations

from wattnot.agentwaste.detect import (
    Call,
    Step,
    canon_args,
    find_cycle,
    find_no_op,
    find_reasoning_overspend,
    find_repeat,
)


def step(*triples: tuple[str, str, str | None]) -> Step:
    return Step(calls=[Call(name=n, args=a, result=r) for n, a, r in triples])


def test_canon_args_orders_and_compacts() -> None:
    assert canon_args({"b": 1, "a": 2}) == '{"a":2,"b":1}'
    assert canon_args('{"b": 1, "a": 2}') == '{"a":2,"b":1}'
    assert canon_args({"b": 1, "a": 2}) == canon_args('{"a":2,"b":1}')


def test_canon_args_keeps_plain_text() -> None:
    assert canon_args("not json at all") == "not json at all"


def test_repeat_fires() -> None:
    steps = [
        step(("read", '{"path":"a"}', "same")),
        step(("read", '{"path":"a"}', "same")),
    ]
    flag = find_repeat(steps, 2)
    assert flag is not None and flag.kind == "repeat"
    assert "read" in flag.detail


def test_repeat_near_miss_reread_after_write() -> None:
    steps = [
        step(("read", '{"path":"a"}', "v1")),
        step(("write", '{"path":"a"}', "ok")),
        step(("read", '{"path":"a"}', "v2")),
    ]
    assert find_repeat(steps, 2) is None


def test_repeat_ignores_calls_without_result() -> None:
    steps = [step(("read", '{"path":"a"}', None)), step(("read", '{"path":"a"}', None))]
    assert find_repeat(steps, 2) is None


def test_no_op_fires_on_duplicate_tail() -> None:
    steps = [
        step(("read", '{"path":"a"}', "v1")),
        step(("read", '{"path":"a"}', "v1")),
        step(("read", '{"path":"a"}', "v1")),
    ]
    flag = find_no_op(steps, 2)
    assert flag is not None and flag.kind == "no_op"


def test_no_op_first_empty_result_is_new() -> None:
    assert find_no_op([step(("read", '{"path":"a"}', ""))], 1) is None


def test_no_op_changed_false_reports_no_change() -> None:
    steps = [
        step(("write", '{"path":"a"}', "ok")),
        step(("write", '{"path":"b"}', '{"changed": false}')),
    ]
    assert find_no_op(steps, 1) is not None


def test_no_op_text_prefix_case_insensitive() -> None:
    steps = [
        step(("run", '{}', "done")),
        step(("run", '{}', "No Change - output identical")),
    ]
    assert find_no_op(steps, 1) is not None


def test_no_op_needs_full_tail() -> None:
    steps = [
        step(("read", '{"path":"a"}', "v1")),
        step(("read", '{"path":"a"}', "v2")),
    ]
    assert find_no_op(steps, 2) is None


def test_cycle_fires_on_abab() -> None:
    steps = [
        step(("a", "{}", "r1")),
        step(("b", "{}", "r2")),
        step(("a", "{}", "r3")),
        step(("b", "{}", "r4")),
    ]
    flag = find_cycle(steps, 4)
    assert flag is not None and flag.kind == "cycle"


def test_cycle_ignores_abc() -> None:
    steps = [
        step(("a", "{}", "r1")),
        step(("b", "{}", "r2")),
        step(("c", "{}", "r3")),
    ]
    assert find_cycle(steps, 4) is None


def test_cycle_ignores_single_repeated_call() -> None:
    steps = [step(("a", "{}", f"r{i}")) for i in range(4)]
    assert find_cycle(steps, 4) is None


def test_reasoning_hard_cap_fires() -> None:
    steps = [Step(reasoning_tokens=100), Step(reasoning_tokens=5000)]
    flag = find_reasoning_overspend(steps, 400, 4.0, 3000)
    assert flag is not None and flag.kind == "reasoning"


def test_reasoning_ratio_fires() -> None:
    steps = [Step(reasoning_tokens=100), Step(reasoning_tokens=120),
             Step(reasoning_tokens=900)]
    flag = find_reasoning_overspend(steps, 400, 4.0, 3000)
    assert flag is not None and flag.kind == "reasoning"


def test_reasoning_needs_two_earlier_steps() -> None:
    steps = [Step(reasoning_tokens=100), Step(reasoning_tokens=900)]
    assert find_reasoning_overspend(steps, 400, 4.0, 3000) is None


def test_reasoning_below_floor_ok() -> None:
    steps = [Step(reasoning_tokens=50), Step(reasoning_tokens=60),
             Step(reasoning_tokens=300)]
    assert find_reasoning_overspend(steps, 400, 4.0, 3000) is None
