"""Tests for Muse Code integration: apply_pr, open-pr remote safety, and snippets."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from wattnot.musecode.apply_pr import (
    REQUIRED_REMOTE,
    apply_pr,
    build_muse_command,
    deterministic_edit_launch_sh,
    deterministic_edit_values_yaml,
    normalize_git_remote,
    parse_muse_jsonl,
)
from wattnot.musecode.snippets import (
    FRAMEWORKS,
    LABEL_MOCK,
    LABEL_VALIDATED,
    generate_and_validate_snippets,
    generate_snippet,
    local_mock_upstream,
)

FIXTURE_DEMO = Path(__file__).resolve().parents[1] / "examples" / "demo-deploy"


def test_fixture_demo_deploy_exists():
    assert FIXTURE_DEMO.exists()
    assert (FIXTURE_DEMO / "launch.sh").exists()
    assert (FIXTURE_DEMO / "values.yaml").exists()


def test_normalize_git_remote():
    assert normalize_git_remote("https://github.com/ak9381/wattnot-demo-deploy.git") == REQUIRED_REMOTE
    assert normalize_git_remote("git@github.com:ak9381/wattnot-demo-deploy.git") == REQUIRED_REMOTE
    assert normalize_git_remote("ssh://git@github.com/ak9381/wattnot-demo-deploy") == REQUIRED_REMOTE
    assert normalize_git_remote("https://github.com/other-org/other-repo.git") == "github.com/other-org/other-repo"


def test_deterministic_edits():
    sh_orig = (
        "#!/usr/bin/env bash\n"
        "# sudo nvidia-smi -pl 400\n"
        "vllm serve meta-llama/Llama-3.1-8B-Instruct --port 8000 --max-num-seqs 256 --gpu-memory-utilization 0.90\n"
    )
    change = {"power_cap_w": 350, "max_num_seqs": 128, "gpu_memory_utilization": 0.85}
    sh_new = deterministic_edit_launch_sh(sh_orig, change)
    assert "sudo nvidia-smi -pl 350" in sh_new
    assert "--max-num-seqs 128" in sh_new
    assert "--gpu-memory-utilization 0.85" in sh_new

    yaml_orig = (
        "vllm:\n"
        "  maxNumSeqs: 256\n"
        "  gpuMemoryUtilization: 0.90\n"
        "  powerCapW: 400\n"
    )
    yaml_new = deterministic_edit_values_yaml(yaml_orig, change)
    assert "powerCapW: 350" in yaml_new
    assert "maxNumSeqs: 128" in yaml_new
    assert "gpuMemoryUtilization: 0.85" in yaml_new


def test_apply_pr_dry_run_produces_patch_and_pr_body(tmp_path):
    repo_copy = tmp_path / "repo"
    shutil.copytree(FIXTURE_DEMO, repo_copy)

    out_dir = tmp_path / "out"
    receipt_hash = "1836f6491abe3cc1e950be4bee7adce3aa0c8f64ff3902dea4d060f971518f61"

    change = {
        "power_cap_w": 350,
        "max_num_seqs": 128,
        "gpu_memory_utilization": 0.85,
        "saving_pct": 12.5,
        "water_ml_per_req": 0.078,
    }
    labels = {
        "power_cap_w": "MEASURED",
        "saving_pct": "MEASURED",
        "p99_delta": "MEASURED",
        "water": "ESTIMATE",
    }

    res = apply_pr(
        change,
        repo_copy,
        receipt_sha256=receipt_hash,
        out_dir=out_dir,
        labels=labels,
        mock=True,
        open_pr=False,
    )

    assert res.patch_path.exists()
    assert res.pr_body_path.exists()
    assert res.patch_sha256
    assert f"wattnot://{receipt_hash}" in res.pr_body
    assert "MEASURED" in res.pr_body
    assert "ESTIMATE" in res.pr_body
    assert "--- a/launch.sh" in res.patch_diff
    assert "+++ b/launch.sh" in res.patch_diff
    # Dry run never mutates the target repo
    assert "nvidia-smi -pl 350" not in (repo_copy / "launch.sh").read_text()
    # p99 was not provided: no fabricated line may appear
    assert "P99 Latency Delta" not in res.pr_body


def test_apply_pr_never_fabricates_measured_metrics(tmp_path):
    repo_copy = tmp_path / "repo"
    shutil.copytree(FIXTURE_DEMO, repo_copy)

    res = apply_pr(
        {"power_cap_w": 350},
        repo_copy,
        receipt_sha256="3" * 64,
        out_dir=tmp_path / "out",
        mock=True,
        open_pr=False,
    )
    assert "Energy Saving" not in res.pr_body
    assert "P99 Latency Delta" not in res.pr_body
    assert "Water Usage" not in res.pr_body
    assert "(label: MEASURED)" not in res.pr_body


def test_open_pr_commits_exactly_the_generated_patch(tmp_path):
    from unittest import mock as _mock

    real_run = subprocess.run
    repo_dir = tmp_path / "repo"
    shutil.copytree(FIXTURE_DEMO, repo_dir)
    real_run(["git", "init", str(repo_dir)], check=True, capture_output=True)
    real_run(["git", "-C", str(repo_dir), "remote", "add", "origin",
              f"https://{REQUIRED_REMOTE}.git"], check=True, capture_output=True)
    real_run(["git", "-C", str(repo_dir), "add", "-A"], check=True, capture_output=True)
    real_run(["git", "-C", str(repo_dir), "-c", "user.email=t@t", "-c", "user.name=t",
              "commit", "-m", "base"], check=True, capture_output=True)

    def fake_run(cmd, **kwargs):
        if cmd[0] == "gh":  # never reach the network from tests
            return subprocess.CompletedProcess(cmd, 1, "", "gh disabled in tests")
        if cmd[:2] == ["git", "-C"] and "commit" in cmd:
            cmd = cmd[:2] + [cmd[2], "-c", "user.email=t@t", "-c", "user.name=t"] + cmd[3:]
        return real_run(cmd, **kwargs)

    with _mock.patch("wattnot.musecode.apply_pr.subprocess.run", side_effect=fake_run):
        res = apply_pr(
            {"power_cap_w": 350},
            repo_dir,
            receipt_sha256="4" * 64,
            out_dir=tmp_path / "out",
            mock=True,
            open_pr=True,
        )

    assert res.open_pr is True
    assert res.pr_url is None  # gh was refused by the test stub
    # The committed tree matches the generated patch content
    assert "nvidia-smi -pl 350" in (repo_dir / "launch.sh").read_text()
    show = real_run(["git", "-C", str(repo_dir), "show", "--stat", "HEAD"],
                    capture_output=True, text=True, check=True)
    assert "launch.sh" in show.stdout
    status = real_run(["git", "-C", str(repo_dir), "status", "--porcelain"],
                      capture_output=True, text=True, check=True)
    assert status.stdout.strip() == ""


def test_apply_pr_open_pr_refuses_when_remote_not_matched(tmp_path):
    repo_dir = tmp_path / "git_repo"
    shutil.copytree(FIXTURE_DEMO, repo_dir)

    # Initialize a git repo with a different remote
    subprocess.run(["git", "init", str(repo_dir)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo_dir), "remote", "add", "origin", "https://github.com/other-user/unauthorized-repo.git"],
        check=True,
        capture_output=True,
    )

    out_dir = tmp_path / "out"
    with pytest.raises(ValueError, match="Refused to open PR"):
        apply_pr(
            {"power_cap_w": 350},
            repo_dir,
            receipt_sha256="0" * 64,
            out_dir=out_dir,
            mock=True,
            open_pr=True,
        )


def test_snippets_generation():
    for fw in FRAMEWORKS:
        snippet = generate_snippet(fw, base_url="http://test-server:8000/v1")
        assert "http://test-server:8000/v1" in snippet
        assert "meta-llama/Llama-3.1-8B-Instruct" in snippet
        assert "X-WattNot-Framework" in snippet


def test_snippets_validation_gracefully_skips_when_not_installed(tmp_path):
    def uninstalled(url, q):
        raise ModuleNotFoundError("Library not installed (simulated)")

    # When libraries are not installed, generate_and_validate_snippets marks results as MOCK (not run)
    results = generate_and_validate_snippets(
        out_dir=tmp_path,
        custom_validators={fw: uninstalled for fw in FRAMEWORKS},
    )
    assert len(results) == len(FRAMEWORKS)
    for r in results:
        assert r.status == "skipped"
        assert r.label == LABEL_MOCK
        assert "Library not installed" in str(r.reason)

    json_file = tmp_path / "snippets_validation.json"
    assert json_file.exists()
    doc = json.loads(json_file.read_text())
    assert len(doc["results"]) == 3


def test_build_muse_command_argv(tmp_path):
    repo = tmp_path / "repo"
    prompt = tmp_path / "out" / "muse_prompt.txt"
    assert build_muse_command(repo, prompt, "my-model") == [
        "muse",
        "exec",
        "--json",
        "--workspace",
        str(repo),
        "--trust-workspace",
        "--disable-approval",
        "--model",
        "my-model",
        "--prompt-file",
        str(prompt),
    ]


def test_parse_muse_jsonl_extracts_ids_and_usage():
    lines = [
        json.dumps({"type": "init", "session_id": "sess-123", "trace_id": "trace-456"}),
        "",
        "not-json{{{",
        json.dumps({"type": "result", "usage": {"input_tokens": 120, "output_tokens": 45, "total_tokens": 165}}),
    ]
    parsed = parse_muse_jsonl(lines)
    assert parsed["session_id"] == "sess-123"
    assert parsed["trace_id"] == "trace-456"
    assert parsed["input_tokens"] == 120
    assert parsed["output_tokens"] == 45
    assert parsed["total_tokens"] == 165
    assert parsed["usage"]["input_tokens"] == 120
    assert parsed["usage"]["total_tokens"] == 165


def test_live_path_records_trace_ids_with_mocked_subprocess(tmp_path):
    from unittest import mock as _mock

    repo_copy = tmp_path / "repo"
    shutil.copytree(FIXTURE_DEMO, repo_copy)
    out_dir = tmp_path / "out"

    muse_stdout = "\n".join(
        [
            json.dumps({"type": "init", "session_id": "sess-live-1", "trace_id": "trace-live-2"}),
            json.dumps({"type": "result", "usage": {"input_tokens": 10, "output_tokens": 20, "total_tokens": 30}}),
        ]
    )
    seen: dict[str, list[str]] = {}

    def fake_run(cmd, capture_output=False, text=False, check=False, **kwargs):
        assert isinstance(cmd, list)
        if cmd[0] == "muse":
            seen["muse"] = list(cmd)
            return subprocess.CompletedProcess(cmd, 0, muse_stdout, "")
        if cmd[0] == "git" and "diff" in cmd:
            return subprocess.CompletedProcess(cmd, 0, "diff --git a/launch.sh b/launch.sh\n", "")
        raise AssertionError(f"unexpected command: {cmd}")

    with _mock.patch("wattnot.musecode.apply_pr.subprocess.run", side_effect=fake_run):
        res = apply_pr(
            {"power_cap_w": 350},
            repo_copy,
            receipt_sha256="1" * 64,
            out_dir=out_dir,
            mock=False,
            open_pr=False,
            model="my-model",
        )

    prompt_file = out_dir.resolve() / "muse_prompt.txt"
    assert seen["muse"] == build_muse_command(repo_copy.resolve(), prompt_file, "my-model")
    assert res.session_id == "sess-live-1"
    assert res.trace_id == "trace-live-2"
    assert "sess-live-1" in res.pr_body
    assert "trace-live-2" in res.pr_body
    doc = json.loads((out_dir / "result.json").read_text(encoding="utf-8"))
    assert doc["session_id"] == "sess-live-1"
    assert doc["trace_id"] == "trace-live-2"
    assert doc["usage"]["total_tokens"] == 30


def test_open_pr_refuses_wrong_remote_with_mocked_subprocess(tmp_path):
    from unittest import mock as _mock

    repo_copy = tmp_path / "repo"
    shutil.copytree(FIXTURE_DEMO, repo_copy)
    out_dir = tmp_path / "out"

    def fake_run(cmd, capture_output=False, text=False, check=False, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, "https://github.com/evil/fork.git\n", "")

    with _mock.patch("wattnot.musecode.apply_pr.subprocess.run", side_effect=fake_run):
        with pytest.raises(ValueError, match="Refused to open PR"):
            apply_pr(
                {"power_cap_w": 350},
                repo_copy,
                receipt_sha256="2" * 64,
                out_dir=out_dir,
                mock=True,
                open_pr=True,
            )


def test_snippets_validation_success_path_with_mock_validator(tmp_path):
    # Custom validator simulating installed framework
    custom_validators = {
        "openai-python": lambda base_url, question: "Simulated answer from mock upstream",
    }
    results = generate_and_validate_snippets(out_dir=tmp_path, custom_validators=custom_validators)

    openai_res = next(r for r in results if r.framework == "openai-python")
    assert openai_res.status == "validated"
    assert openai_res.label == LABEL_VALIDATED
    assert openai_res.response == "Simulated answer from mock upstream"


def test_parse_muse_v14_run_usage():
    lines = [
        '{"stream":{"kind":"session","id":"S1"},"payload_type":"x","payload":{}}',
        '{"stream":{"kind":"run","id":"R1"},"payload_type":"run.usage","payload":{"run_stream":{"id":"R1"},'
        '"models":[{"input_tokens":10,"output_tokens":3}]}}',
    ]
    out = parse_muse_jsonl(lines)
    assert out["session_id"] == "S1" and out["trace_id"] == "R1"
    assert out["input_tokens"] == 10 and out["total_tokens"] == 13
