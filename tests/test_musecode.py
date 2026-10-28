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
    deterministic_edit_launch_sh,
    deterministic_edit_values_yaml,
    normalize_git_remote,
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
