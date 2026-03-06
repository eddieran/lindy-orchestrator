"""GitHub Issues tracker provider using the `gh` CLI."""

from __future__ import annotations

import json
import subprocess

from .base import TrackerIssue, TrackerProvider


class GitHubIssuesProvider:
    """Fetch and update GitHub Issues using the `gh` CLI.

    Requires `gh` to be installed and authenticated.
    No API token management needed — uses `gh auth` session.
    """

    def __init__(self, repo: str = "") -> None:
        self.repo = repo  # e.g., "owner/repo"; empty = current repo

    def _gh(self, args: list[str], check: bool = True) -> subprocess.CompletedProcess:
        cmd = ["gh"] + args
        if self.repo:
            cmd.extend(["--repo", self.repo])
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
            check=check,
        )

    def fetch_issues(
        self,
        project: str = "",
        labels: list[str] | None = None,
        status: str = "open",
        limit: int = 20,
    ) -> list[TrackerIssue]:
        """Fetch issues from GitHub using `gh issue list`."""
        args = [
            "issue",
            "list",
            "--state",
            status,
            "--limit",
            str(limit),
            "--json",
            "number,title,body,labels,state,url",
        ]
        if labels:
            args.extend(["--label", ",".join(labels)])

        result = self._gh(args, check=False)
        if result.returncode != 0:
            return []

        try:
            raw_issues = json.loads(result.stdout)
        except json.JSONDecodeError:
            return []

        issues = []
        for raw in raw_issues:
            label_names = [lbl.get("name", "") for lbl in raw.get("labels", [])]
            issues.append(
                TrackerIssue(
                    id=str(raw.get("number", "")),
                    title=raw.get("title", ""),
                    body=raw.get("body", ""),
                    labels=label_names,
                    status=raw.get("state", "open").lower(),
                    url=raw.get("url", ""),
                )
            )
        return issues

    def update_status(self, issue_id: str, status: str, comment: str = "") -> bool:
        """Close or reopen an issue."""
        action = "close" if status in ("closed", "done", "completed") else "reopen"
        result = self._gh(["issue", action, issue_id], check=False)

        if comment:
            self.add_comment(issue_id, comment)

        return result.returncode == 0

    def add_comment(self, issue_id: str, comment: str) -> bool:
        """Add a comment to an issue."""
        result = self._gh(
            ["issue", "comment", issue_id, "--body", comment],
            check=False,
        )
        return result.returncode == 0


# Verify TrackerProvider compliance
assert isinstance(GitHubIssuesProvider, type)
_provider_check: TrackerProvider = GitHubIssuesProvider()  # type: ignore[assignment]
