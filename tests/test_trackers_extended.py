"""Extended tests for trackers — timeout handling, edge cases."""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from lindy_orchestrator.trackers.base import TrackerIssue, TrackerProvider
from lindy_orchestrator.trackers.factory import create_tracker
from lindy_orchestrator.trackers.github_issues import GitHubIssuesProvider


class TestGitHubProviderTimeoutHandling:
    @patch("lindy_orchestrator.trackers.github_issues.subprocess.run")
    def test_fetch_issues_timeout(self, mock_run):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="gh", timeout=30)
        provider = GitHubIssuesProvider()
        with pytest.raises(subprocess.TimeoutExpired):
            provider.fetch_issues(project="test")

    @patch("lindy_orchestrator.trackers.github_issues.subprocess.run")
    def test_update_status_timeout(self, mock_run):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="gh", timeout=30)
        provider = GitHubIssuesProvider()
        with pytest.raises(subprocess.TimeoutExpired):
            provider.update_status("1", "closed")

    @patch("lindy_orchestrator.trackers.github_issues.subprocess.run")
    def test_add_comment_timeout(self, mock_run):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="gh", timeout=30)
        provider = GitHubIssuesProvider()
        with pytest.raises(subprocess.TimeoutExpired):
            provider.add_comment("1", "Hello")


class TestGitHubProviderStatusMapping:
    @patch("lindy_orchestrator.trackers.github_issues.subprocess.run")
    def test_done_maps_to_close(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        provider = GitHubIssuesProvider()
        provider.update_status("1", "done")
        args = mock_run.call_args[0][0]
        assert "close" in args

    @patch("lindy_orchestrator.trackers.github_issues.subprocess.run")
    def test_completed_maps_to_close(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        provider = GitHubIssuesProvider()
        provider.update_status("1", "completed")
        args = mock_run.call_args[0][0]
        assert "close" in args

    @patch("lindy_orchestrator.trackers.github_issues.subprocess.run")
    def test_unknown_status_maps_to_reopen(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        provider = GitHubIssuesProvider()
        provider.update_status("1", "in_progress")
        args = mock_run.call_args[0][0]
        assert "reopen" in args

    @patch("lindy_orchestrator.trackers.github_issues.subprocess.run")
    def test_update_status_failure_returns_false(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1)
        provider = GitHubIssuesProvider()
        result = provider.update_status("1", "closed")
        assert result is False


class TestTrackerFactoryEdgeCases:
    def test_create_github_default(self):
        tracker = create_tracker()
        assert isinstance(tracker, GitHubIssuesProvider)

    def test_create_with_unknown_provider(self):
        with pytest.raises(ValueError, match="Unknown tracker provider.*linear"):
            create_tracker("linear")

    def test_github_provider_satisfies_protocol(self):
        provider = GitHubIssuesProvider()
        assert isinstance(provider, TrackerProvider)


class TestTrackerIssueEdgeCases:
    def test_issue_with_all_defaults(self):
        issue = TrackerIssue(id="0", title="", body="")
        assert issue.status == "open"
        assert issue.labels == []
        assert issue.url == ""
        assert issue.priority == "normal"

    def test_issue_labels_are_independent(self):
        issue1 = TrackerIssue(id="1", title="a", body="")
        issue2 = TrackerIssue(id="2", title="b", body="")
        issue1.labels.append("bug")
        assert issue2.labels == []  # Should not be shared


class TestGitHubProviderGhCommand:
    @patch("lindy_orchestrator.trackers.github_issues.subprocess.run")
    def test_gh_with_repo_adds_flag(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="[]")
        provider = GitHubIssuesProvider(repo="org/repo")
        provider._gh(["issue", "list"], check=False)
        args = mock_run.call_args[0][0]
        assert "--repo" in args
        assert "org/repo" in args

    @patch("lindy_orchestrator.trackers.github_issues.subprocess.run")
    def test_gh_without_repo_no_flag(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="[]")
        provider = GitHubIssuesProvider(repo="")
        provider._gh(["issue", "list"], check=False)
        args = mock_run.call_args[0][0]
        assert "--repo" not in args

    @patch("lindy_orchestrator.trackers.github_issues.subprocess.run")
    def test_gh_check_true_raises_on_failure(self, mock_run):
        mock_run.side_effect = subprocess.CalledProcessError(1, "gh")
        provider = GitHubIssuesProvider()
        with pytest.raises(subprocess.CalledProcessError):
            provider._gh(["issue", "list"], check=True)
