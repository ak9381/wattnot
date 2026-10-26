"""Tests for the M3 runner's workload format and loader.

T08 offline part: the public replay set and the Muse Code task set v1 share
one JSONL format and one loader. These tests pin the format (required fields,
comment and blank-line tolerance, duplicate-id and type errors) and check that
the two committed sample files load and look sane. No network, no model API.
"""

from __future__ import annotations

import json

import pytest

from wattnot.runner import workload as wl


def _write(path, lines):
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _item(**over):
    base = {"id": "a1", "prompt": "hello", "max_tokens": 16}
    base.update(over)
    return json.dumps(base)


# --- format: a good file loads ------------------------------------------------


def test_load_minimal_item(tmp_path):
    p = _write(tmp_path / "w.jsonl", [_item()])
    w = wl.load_workload(p)
    assert len(w) == 1
    item = w.items[0]
    assert item.id == "a1"
    assert item.prompt == "hello"
    assert item.max_tokens == 16
    assert item.meta == {}


def test_extra_fields_go_to_meta(tmp_path):
    p = _write(tmp_path / "w.jsonl", [_item(source="handwritten", tags=["qa"])])
    w = wl.load_workload(p)
    assert w.items[0].meta == {"source": "handwritten", "tags": ["qa"]}


def test_name_and_version_from_filename(tmp_path):
    p = _write(tmp_path / "replay-v1.jsonl", [_item()])
    w = wl.load_workload(p)
    assert w.name == "replay-v1"
    assert w.version == "v1"


def test_kind_is_passed_through(tmp_path):
    p = _write(tmp_path / "w.jsonl", [_item()])
    w = wl.load_workload(p, kind="muse-code")
    assert w.kind == "muse-code"


def test_workload_is_iterable_and_indexable(tmp_path):
    p = _write(tmp_path / "w.jsonl", [_item(id="a1"), _item(id="a2")])
    w = wl.load_workload(p)
    assert [i.id for i in w] == ["a1", "a2"]
    assert w[1].id == "a2"
    assert w.by_id("a2").prompt == "hello"


# --- format: comments and blank lines are skipped -----------------------------


def test_comments_and_blank_lines_skipped(tmp_path):
    p = _write(
        tmp_path / "w.jsonl",
        ["# TODO: replace with the real public set", "", _item(), "   ", "# trailing"],
    )
    w = wl.load_workload(p)
    assert len(w) == 1


# --- format: bad inputs are rejected with clear errors ------------------------


def test_empty_file_is_an_error(tmp_path):
    p = _write(tmp_path / "w.jsonl", ["# only a comment"])
    with pytest.raises(wl.WorkloadError, match="no items"):
        wl.load_workload(p)


def test_invalid_json_line(tmp_path):
    p = _write(tmp_path / "w.jsonl", ["{not json}"])
    with pytest.raises(wl.WorkloadError, match="invalid JSON"):
        wl.load_workload(p)


def test_non_object_line(tmp_path):
    p = _write(tmp_path / "w.jsonl", ["[1, 2, 3]"])
    with pytest.raises(wl.WorkloadError, match="object"):
        wl.load_workload(p)


@pytest.mark.parametrize(
    "bad,msg",
    [
        ('{"prompt": "x", "max_tokens": 4}', "id"),
        ('{"id": "", "prompt": "x", "max_tokens": 4}', "id"),
        ('{"id": "a", "max_tokens": 4}', "prompt"),
        ('{"id": "a", "prompt": "", "max_tokens": 4}', "prompt"),
        ('{"id": "a", "prompt": "x"}', "max_tokens"),
        ('{"id": "a", "prompt": "x", "max_tokens": 0}', "max_tokens"),
        ('{"id": "a", "prompt": "x", "max_tokens": -1}', "max_tokens"),
        ('{"id": "a", "prompt": "x", "max_tokens": 1.5}', "max_tokens"),
        ('{"id": "a", "prompt": "x", "max_tokens": true}', "max_tokens"),
    ],
)
def test_bad_fields_rejected(tmp_path, bad, msg):
    p = _write(tmp_path / "w.jsonl", [bad])
    with pytest.raises(wl.WorkloadError, match=msg):
        wl.load_workload(p)


def test_duplicate_id_rejected(tmp_path):
    p = _write(tmp_path / "w.jsonl", [_item(id="dup"), _item(id="dup")])
    with pytest.raises(wl.WorkloadError, match="duplicate id 'dup'"):
        wl.load_workload(p)


def test_error_names_the_line_number(tmp_path):
    p = _write(tmp_path / "w.jsonl", [_item(), "{bad}"])
    with pytest.raises(wl.WorkloadError, match=r":2:"):
        wl.load_workload(p)


# --- the two committed sample sets --------------------------------------------


def test_committed_replay_set_loads():
    w = wl.load_replay_set()
    assert w.kind == "replay"
    assert w.name == "replay-v1"
    assert len(w) >= 1
    for item in w:
        assert item.prompt.strip()
        assert item.max_tokens > 0


def test_committed_muse_code_set_loads():
    w = wl.load_muse_code_set()
    assert w.kind == "muse-code"
    assert w.name == "muse-code-v1"
    assert len(w) >= 1
    for item in w:
        assert item.prompt.strip()
        assert item.max_tokens > 0


def test_sample_sets_have_unique_ids_across_each_file():
    for w in (wl.load_replay_set(), wl.load_muse_code_set()):
        ids = [i.id for i in w]
        assert len(ids) == len(set(ids))
