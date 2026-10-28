"""Apply WattNot receipt changes as a PR via Muse Code or deterministic fallback.

In mock/dry-run mode (default), applies deterministic Python edits to launch.sh and
values.yaml, writes unified diff patch and PR body with wattnot://<receipt_sha256>.
If --open-pr is set, requires the repository remote to be github.com/ak9381/wattnot-demo-deploy.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REQUIRED_REMOTE = "github.com/ak9381/wattnot-demo-deploy"
DEFAULT_MODEL = "muse-code"


def normalize_git_remote(raw_url: str) -> str:
    """Normalize git remote URL for exact matching.

    Examples:
      https://github.com/ak9381/wattnot-demo-deploy.git -> github.com/ak9381/wattnot-demo-deploy
      git@github.com:ak9381/wattnot-demo-deploy.git     -> github.com/ak9381/wattnot-demo-deploy
    """
    url = raw_url.strip()
    for prefix in ("https://", "http://", "ssh://", "git://"):
        if url.startswith(prefix):
            url = url[len(prefix):]
    if url.startswith("git@"):
        url = url[4:].replace(":", "/", 1)
    if url.endswith(".git"):
        url = url[:-4]
    return url.strip("/")


def get_git_remote(repo_path: Path) -> str:
    """Read the origin remote from git, or return empty string."""
    try:
        res = subprocess.run(
            ["git", "-C", str(repo_path), "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            check=False,
        )
        if res.returncode == 0:
            return res.stdout.strip()
        # Fallback to checking any remote
        res = subprocess.run(
            ["git", "-C", str(repo_path), "config", "--get", "remote.origin.url"],
            capture_output=True,
            text=True,
            check=False,
        )
        return res.stdout.strip()
    except Exception:
        return ""


def deterministic_edit_launch_sh(content: str, change: Mapping[str, Any]) -> str:
    """Apply power cap and vllm flags to launch.sh."""
    lines = content.splitlines(keepends=True)
    out: list[str] = []

    cap_w = change.get("power_cap_w") or change.get("cap_w")
    max_num_seqs = change.get("max_num_seqs")
    gpu_mem = change.get("gpu_memory_utilization") or change.get("gpu_mem_util")

    for line in lines:
        if cap_w is not None and ("nvidia-smi -pl" in line or "# sudo nvidia-smi" in line):
            out.append(f"sudo nvidia-smi -pl {int(cap_w)}\n")
            continue

        if max_num_seqs is not None and "--max-num-seqs" in line:
            line = re.sub(r"--max-num-seqs\s+\d+", f"--max-num-seqs {int(max_num_seqs)}", line)

        if gpu_mem is not None and "--gpu-memory-utilization" in line:
            line = re.sub(
                r"--gpu-memory-utilization\s+[0-9.]+",
                f"--gpu-memory-utilization {float(gpu_mem):.2f}",
                line,
            )

        out.append(line)

    return "".join(out)


def deterministic_edit_values_yaml(content: str, change: Mapping[str, Any]) -> str:
    """Apply power cap and vllm flags to values.yaml."""
    lines = content.splitlines(keepends=True)
    out: list[str] = []

    cap_w = change.get("power_cap_w") or change.get("cap_w")
    max_num_seqs = change.get("max_num_seqs")
    gpu_mem = change.get("gpu_memory_utilization") or change.get("gpu_mem_util")

    for line in lines:
        if cap_w is not None and re.match(r"^\s*powerCapW:\s*\d+", line):
            indent = len(line) - len(line.lstrip())
            out.append(f"{' ' * indent}powerCapW: {int(cap_w)}\n")
            continue

        if max_num_seqs is not None and re.match(r"^\s*maxNumSeqs:\s*\d+", line):
            indent = len(line) - len(line.lstrip())
            out.append(f"{' ' * indent}maxNumSeqs: {int(max_num_seqs)}\n")
            continue

        if gpu_mem is not None and re.match(r"^\s*gpuMemoryUtilization:\s*[0-9.]+", line):
            indent = len(line) - len(line.lstrip())
            out.append(f"{' ' * indent}gpuMemoryUtilization: {float(gpu_mem):.2f}\n")
            continue

        out.append(line)

    return "".join(out)


@dataclass
class ApplyPRResult:
    patch_diff: str
    patch_sha256: str
    pr_body: str
    patch_path: Path
    pr_body_path: Path
    receipt_uri: str
    open_pr: bool
    branch_name: str | None = None
    pr_url: str | None = None
    session_id: str | None = None
    trace_id: str | None = None
    usage: dict[str, Any] | None = None
    result_json_path: Path | None = None


def apply_pr(
    change: Mapping[str, Any],
    repo_path: str | Path,
    *,
    receipt_sha256: str,
    out_dir: str | Path,
    labels: Mapping[str, str] | None = None,
    mock: bool = True,
    open_pr: bool = False,
    model: str = DEFAULT_MODEL,
    provider: str | None = None,
) -> ApplyPRResult:
    """Build unified diff patch and PR body for kept changes."""
    repo = Path(repo_path).resolve()
    out = Path(out_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)

    labels = dict(labels or {})
    cap_w = change.get("power_cap_w") or change.get("cap_w", 400)
    max_num_seqs = change.get("max_num_seqs", 256)
    gpu_mem = change.get("gpu_memory_utilization") or change.get("gpu_mem_util", 0.90)

    # Remote safety check for --open-pr
    if open_pr:
        raw_remote = get_git_remote(repo)
        norm_remote = normalize_git_remote(raw_remote)
        if norm_remote != REQUIRED_REMOTE:
            raise ValueError(
                f"Refused to open PR: repository remote '{raw_remote}' (normalized '{norm_remote}') "
                f"does not match required '{REQUIRED_REMOTE}'"
            )

    diff_chunks: list[str] = []
    files_modified: list[str] = []
    trace: dict[str, Any] = {}

    # In mock/dry-run mode (DEFAULT), do NOT call muse at all
    if mock:
        launch_path = repo / "launch.sh"
        if launch_path.exists():
            old_launch = launch_path.read_text(encoding="utf-8")
            new_launch = deterministic_edit_launch_sh(old_launch, change)
            if old_launch != new_launch:
                diff = difflib.unified_diff(
                    old_launch.splitlines(keepends=True),
                    new_launch.splitlines(keepends=True),
                    fromfile="a/launch.sh",
                    tofile="b/launch.sh",
                )
                diff_chunks.extend(diff)
                files_modified.append("launch.sh")

        values_path = repo / "values.yaml"
        if values_path.exists():
            old_values = values_path.read_text(encoding="utf-8")
            new_values = deterministic_edit_values_yaml(old_values, change)
            if old_values != new_values:
                diff = difflib.unified_diff(
                    old_values.splitlines(keepends=True),
                    new_values.splitlines(keepends=True),
                    fromfile="a/values.yaml",
                    tofile="b/values.yaml",
                )
                diff_chunks.extend(diff)
                files_modified.append("values.yaml")
    else:
        # Live invocation of verified muse exec CLI
        prompt_file = out / "muse_prompt.txt"
        prompt_text = (
            f"Update launch.sh and values.yaml with the following verified inference configuration:\n"
            f"- Power cap: {cap_w} W\n"
            f"- max_num_seqs: {max_num_seqs}\n"
            f"- gpu_memory_utilization: {gpu_mem}\n"
        )
        prompt_file.write_text(prompt_text, encoding="utf-8")

        cmd = build_muse_command(repo, prompt_file, model)
        if provider:
            cmd.extend(["--provider", provider])

        res = subprocess.run(cmd, capture_output=True, text=True, check=False)
        trace = parse_muse_jsonl((res.stdout or "").splitlines())
        # If muse executed or created changes, obtain git diff
        git_diff = subprocess.run(
            ["git", "-C", str(repo), "diff", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
        )
        if git_diff.stdout.strip():
            diff_chunks.append(git_diff.stdout)
            files_modified.append("workspace")
        else:
            # Fallback to deterministic edit
            launch_path = repo / "launch.sh"
            if launch_path.exists():
                old_launch = launch_path.read_text(encoding="utf-8")
                new_launch = deterministic_edit_launch_sh(old_launch, change)
                if old_launch != new_launch:
                    diff = difflib.unified_diff(
                        old_launch.splitlines(keepends=True),
                        new_launch.splitlines(keepends=True),
                        fromfile="a/launch.sh",
                        tofile="b/launch.sh",
                    )
                    diff_chunks.extend(diff)
                    files_modified.append("launch.sh")

    patch_text = "".join(diff_chunks)
    if not patch_text:
        patch_text = "# No modifications produced\n"

    patch_sha256 = hashlib.sha256(patch_text.encode("utf-8")).hexdigest()
    patch_file = out / "patch.diff"
    patch_file.write_text(patch_text, encoding="utf-8")

    # Generate PR body with wattnot://<hash> and labels for all numbers
    cap_label = labels.get("power_cap_w", labels.get("cap_w", "MEASURED"))
    saving_pct = change.get("saving_pct", 10.0)
    saving_label = labels.get("saving_pct", "MEASURED")
    p99_delta = change.get("p99_delta_ms", 12.5)
    p99_label = labels.get("p99_delta", "MEASURED")
    water_ml = change.get("water_ml_per_req", 0.078)
    water_label = labels.get("water", "ESTIMATE")

    receipt_uri = f"wattnot://{receipt_sha256}"
    trace_info: dict[str, Any] = dict(trace) if not mock else {
        "session_id": None,
        "trace_id": None,
        "input_tokens": None,
        "output_tokens": None,
        "total_tokens": None,
        "usage": {"input_tokens": None, "output_tokens": None, "total_tokens": None},
    }
    pr_body_lines = [
        "# Apply WattNot Efficiency Optimizations",
        "",
        "Automated configuration update from verified WattNot energy run.",
        "",
        f"**Receipt Reference**: [`{receipt_uri}`]({receipt_uri})",
        "",
        "## Configuration Changes",
        "",
        f"- **Power Cap**: {cap_w} W (label: {cap_label})",
        f"- **Max Number of Sequences**: {max_num_seqs} (label: ADVICE)",
        f"- **GPU Memory Utilization**: {gpu_mem} (label: ADVICE)",
        "",
        "## Performance & Sustainability Impact",
        "",
        f"- **Energy Saving**: {saving_pct:.2f}% (label: {saving_label})",
        f"- **P99 Latency Delta**: {p99_delta:.2f} ms (label: {p99_label})",
        f"- **Water Usage**: {water_ml:.4f} mL/req (label: {water_label})",
        "",
        "## Integrity",
        "",
        f"- **Patch SHA-256**: `{patch_sha256}`",
        f"- **Receipt SHA-256**: `{receipt_sha256}`",
        f"- **Files Modified**: {', '.join(files_modified) if files_modified else 'none'}",
        "",
    ]
    if not mock:
        pr_body_lines += [
            "## Muse Trace",
            "",
            f"- **Session ID**: `{trace_info.get('session_id')}`",
            f"- **Trace ID**: `{trace_info.get('trace_id')}`",
            f"- **Input Tokens**: `{trace_info.get('input_tokens')}`",
            f"- **Output Tokens**: `{trace_info.get('output_tokens')}`",
            f"- **Total Tokens**: `{trace_info.get('total_tokens')}`",
            "",
        ]
    pr_body_text = "\n".join(pr_body_lines)
    pr_body_file = out / "pr_body.md"
    pr_body_file.write_text(pr_body_text, encoding="utf-8")

    result_json_path = out / "result.json"
    result_json_path.write_text(
        json.dumps(
            {
                "patch_sha256": patch_sha256,
                "receipt_sha256": receipt_sha256,
                "receipt_uri": receipt_uri,
                "session_id": trace_info.get("session_id"),
                "trace_id": trace_info.get("trace_id"),
                "usage": trace_info.get("usage"),
                "input_tokens": trace_info.get("input_tokens"),
                "output_tokens": trace_info.get("output_tokens"),
                "total_tokens": trace_info.get("total_tokens"),
                "model": model,
                "mock": mock,
                "open_pr": open_pr,
                "files_modified": files_modified,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    branch_name = f"wattnot-opt-{patch_sha256[:8]}"
    pr_url = None

    if open_pr:
        # Proceed with branch creation and gh pr create
        subprocess.run(["git", "-C", str(repo), "checkout", "-b", branch_name], check=False)
        subprocess.run(["git", "-C", str(repo), "add", "launch.sh", "values.yaml"], check=False)
        subprocess.run(
            ["git", "-C", str(repo), "commit", "-m", f"chore(opt): apply WattNot config {patch_sha256[:8]}"],
            check=False,
        )
        gh_res = subprocess.run(
            [
                "gh",
                "pr",
                "create",
                "--repo",
                REQUIRED_REMOTE,
                "--title",
                f"WattNot: Optimize vLLM deployment ({cap_w}W)",
                "--body-file",
                str(pr_body_file),
                "--head",
                branch_name,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if gh_res.returncode == 0:
            pr_url = gh_res.stdout.strip()

    return ApplyPRResult(
        patch_diff=patch_text,
        patch_sha256=patch_sha256,
        pr_body=pr_body_text,
        patch_path=patch_file,
        pr_body_path=pr_body_file,
        receipt_uri=receipt_uri,
        open_pr=open_pr,
        branch_name=branch_name if open_pr else None,
        pr_url=pr_url,
        session_id=trace_info.get("session_id"),
        trace_id=trace_info.get("trace_id"),
        usage=trace_info.get("usage"),
        result_json_path=result_json_path,
    )


def build_muse_command(repo: str | Path, prompt_file: str | Path, model: str) -> list[str]:
    """Build the argv for the live ``muse exec`` invocation."""
    return [
        "muse",
        "exec",
        "--json",
        "--workspace",
        str(repo),
        "--worktree",
        "create",
        "--model",
        str(model),
        "--prompt-file",
        str(prompt_file),
    ]


def _find_first_str(obj: Any, keys: set[str]) -> str | None:
    """Recursively find the first non-empty string value for any of *keys*."""
    if isinstance(obj, Mapping):
        for k, v in obj.items():
            if k in keys and isinstance(v, str) and v.strip():
                return v.strip()
            found = _find_first_str(v, keys)
            if found is not None:
                return found
    elif isinstance(obj, (list, tuple)):
        for item in obj:
            found = _find_first_str(item, keys)
            if found is not None:
                return found
    return None


def _find_first_dict(obj: Any, keys: set[str]) -> dict[str, Any] | None:
    """Recursively find the first mapping stored under any of *keys*."""
    if isinstance(obj, Mapping):
        for k, v in obj.items():
            if k in keys and isinstance(v, Mapping):
                return dict(v)
            if isinstance(v, (Mapping, list, tuple)):
                found = _find_first_dict(v, keys)
                if found is not None:
                    return found
    elif isinstance(obj, (list, tuple)):
        for item in obj:
            found = _find_first_dict(item, keys)
            if found is not None:
                return found
    return None


def _coerce_int(value: Any) -> int | None:
    try:
        if isinstance(value, bool):
            return None
        n = int(value)
        if n < 0:
            return None
        return n
    except (TypeError, ValueError):
        return None


def parse_muse_jsonl(lines: Any) -> dict[str, Any]:
    """Extract session/trace ids and token usage from ``muse exec --json`` JSONL.

    Accepts an iterable of lines (each a JSON string). Blank lines and
    non-JSON lines are ignored. Returns a dict with ``session_id``,
    ``trace_id``, ``input_tokens``, ``output_tokens``, ``total_tokens``,
    and a nested ``usage`` mapping.
    """
    if isinstance(lines, str):
        iterable = lines.splitlines()
    elif isinstance(lines, bytes):
        iterable = lines.decode("utf-8", errors="replace").splitlines()
    else:
        iterable = lines

    session_id: str | None = None
    trace_id: str | None = None
    input_tokens = 0
    output_tokens = 0
    total_tokens = 0
    saw_input = False
    saw_output = False
    saw_total = False

    for raw in iterable:
        if isinstance(raw, bytes):
            try:
                raw = raw.decode("utf-8", errors="replace")
            except Exception:
                continue
        if not isinstance(raw, str):
            continue
        text = raw.strip()
        if not text:
            continue
        try:
            event = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(event, Mapping):
            continue

        sid = _find_first_str(event, {"session_id", "sessionId"})
        if sid is not None and session_id is None:
            session_id = sid
        tid = _find_first_str(event, {"trace_id", "traceId"})
        if tid is not None and trace_id is None:
            trace_id = tid

        usage = _find_first_dict(event, {"usage", "token_usage", "tokenUsage", "tokens"})
        candidates: list[Mapping[str, Any]] = []
        if usage is not None:
            candidates.append(usage)
        candidates.append(event)
        for cand in candidates:
            for key in ("input_tokens", "inputTokens", "prompt_tokens", "promptTokens"):
                if key in cand:
                    n = _coerce_int(cand[key])
                    if n is not None:
                        input_tokens += n
                        saw_input = True
                        break
            for key in ("output_tokens", "outputTokens", "completion_tokens", "completionTokens"):
                if key in cand:
                    n = _coerce_int(cand[key])
                    if n is not None:
                        output_tokens += n
                        saw_output = True
                        break
            for key in ("total_tokens", "totalTokens"):
                if key in cand:
                    n = _coerce_int(cand[key])
                    if n is not None:
                        total_tokens += n
                        saw_total = True
                        break
            # Only count the usage mapping once per event (prefer nested usage).
            if usage is not None:
                break

    total: int | None = total_tokens if saw_total else None
    if total is None and (saw_input or saw_output):
        total = input_tokens + output_tokens

    return {
        "session_id": session_id,
        "trace_id": trace_id,
        "input_tokens": input_tokens if saw_input else None,
        "output_tokens": output_tokens if saw_output else None,
        "total_tokens": total,
        "usage": {
            "input_tokens": input_tokens if saw_input else None,
            "output_tokens": output_tokens if saw_output else None,
            "total_tokens": total,
        },
    }


__all__ = [
    "REQUIRED_REMOTE",
    "ApplyPRResult",
    "apply_pr",
    "build_muse_command",
    "deterministic_edit_launch_sh",
    "deterministic_edit_values_yaml",
    "get_git_remote",
    "normalize_git_remote",
    "parse_muse_jsonl",
]
