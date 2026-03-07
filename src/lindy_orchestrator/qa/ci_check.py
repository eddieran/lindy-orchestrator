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
        timeout_seconds: int (default 900)
        poll_interval: int (default 30)
    """

    def check(
        self,
        params: dict[str, Any],
        project_root: Path,
        module_name: str = "",
        task_output: str = "",
        **kwargs: Any,
    ) -> QAResult:
        repo = params.get("repo", "")
        workflow = params.get("workflow", "ci.yml")
        branch = params.get("branch", "")
        timeout_seconds = params.get("timeout_seconds", 900)
        poll_interval = params.get("poll_interval", 30)

        if not repo or not branch:
            return QAResult(
                gate="ci_check",
                passed=False,
                output=f"Missing required params: repo={repo!r}, branch={branch!r}",
            )

        # Quick check: if a completed run already exists (e.g. on retry),
        # return immediately without entering the polling loop.
        quick = self._query_runs(repo, workflow, branch)
        if quick is not None:
            return quick

        deadline = time.monotonic() + timeout_seconds

        while time.monotonic() < deadline:
            time.sleep(poll_interval)

            result = self._query_runs(repo, workflow, branch)
            if result is not None:
                return result

            # _query_runs returns None when no completed run yet — continue polling

        return QAResult(
            gate="ci_check",
            passed=False,
            output=f"CI timed out after {timeout_seconds}s on {repo}@{branch}",
            details={"repo": repo, "branch": branch, "timeout": True},
        )

    def _query_runs(self, repo: str, workflow: str, branch: str) -> QAResult | None:
        """Query GitHub Actions for the latest run on a branch.

        Returns a QAResult if a completed run is found (pass or fail),
        or None if no completed run exists yet (caller should keep polling).
        Raises no exceptions — errors are returned as failed QAResult.
        """
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
            return None  # transient — keep polling

        if proc.returncode != 0:
            return None  # transient — keep polling

        try:
            runs = json.loads(proc.stdout)
        except json.JSONDecodeError:
            return None  # transient — keep polling

        if not runs:
            return None  # no runs yet — keep polling

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

        return None  # still running — keep polling
