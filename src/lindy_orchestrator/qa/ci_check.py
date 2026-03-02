"""CI check gate: polls GitHub Actions status via `gh` CLI."""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from typing import Any

from ..models import QAResult
from . import register


@register("ci_check")
class CICheckGate:
    """Polls GitHub Actions CI status for a branch.

    params:
        repo: str (e.g., "myorg/my-backend")
        branch: str (e.g., "af/task-1")
        workflow: str (default "ci.yml")
        timeout_seconds: int (default 600)
        poll_interval: int (default 30)
    """

    def check(
        self,
        params: dict[str, Any],
        project_root: Path,
        module_name: str = "",
        task_output: str = "",
        **kwargs,
    ) -> QAResult:
        repo = params.get("repo", "")
        workflow = params.get("workflow", "ci.yml")
        branch = params.get("branch", "")
        timeout_seconds = params.get("timeout_seconds", 600)
        poll_interval = params.get("poll_interval", 30)

        if not repo or not branch:
            return QAResult(
                gate="ci_check",
                passed=False,
                output=f"Missing required params: repo={repo!r}, branch={branch!r}",
            )

        deadline = time.monotonic() + timeout_seconds
        last_error = ""

        while time.monotonic() < deadline:
            try:
                proc = subprocess.run(
                    [
                        "gh",
                        "run",
                        "list",
                        "--repo",
                        repo,
                        "--workflow",
                        workflow,
                        "--branch",
                        branch,
                        "--limit",
                        "1",
                        "--json",
                        "status,conclusion,url,databaseId",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
            except FileNotFoundError:
                return QAResult(
                    gate="ci_check",
                    passed=False,
                    output="gh CLI not found in PATH",
                )
            except subprocess.TimeoutExpired:
                last_error = "gh CLI call timed out"
                time.sleep(poll_interval)
                continue

            if proc.returncode != 0:
                last_error = proc.stderr.strip()[:200]
                time.sleep(poll_interval)
                continue

            try:
                runs = json.loads(proc.stdout)
            except json.JSONDecodeError:
                last_error = f"Invalid JSON: {proc.stdout[:200]}"
                time.sleep(poll_interval)
                continue

            if not runs:
                time.sleep(poll_interval)
                continue

            run = runs[0]
            if run["status"] == "completed":
                passed = run.get("conclusion") == "success"
                run_url = run.get("url", "")
                return QAResult(
                    gate="ci_check",
                    passed=passed,
                    output=f"CI {run.get('conclusion', 'unknown')} on {repo}@{branch} ({run_url})",
                    details={
                        "repo": repo,
                        "branch": branch,
                        "conclusion": run.get("conclusion", ""),
                        "run_url": run_url,
                        "run_id": run.get("databaseId", 0),
                    },
                )

            time.sleep(poll_interval)

        return QAResult(
            gate="ci_check",
            passed=False,
            output=f"CI timed out after {timeout_seconds}s on {repo}@{branch}. Last: {last_error}",
            details={"repo": repo, "branch": branch, "timeout": True},
        )
