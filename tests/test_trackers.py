"""Tests for issue tracker integration."""

from __future__ import annotations

import json
from dataclasses import asdict
from unittest.mock import MagicMock, patch

import pytest

from lindy_orchestrator.config import OrchestratorConfig, TrackerConfig
from lindy_orchestrator.trackers import TrackerIssue, TrackerProvider, create_tracker
from lindy_orchestrator.trackers.github_issues import GitHubIssuesProvider


class TestTrackerIssue:
    def test_defaults(self):
        issue = TrackerIssue(id="1", title="Bug", body="Fix it")
        assert issue.status == "open"
        assert issue.priority == "normal"
        assert issue.labels == []
        assert issue.url == ""

    def test_full_fields(self):
        issue = TrackerIssue(
            id="42",
            title="Add feature",
            body="Need this feature",
            labels=["enhancement", "orchestrator"],
            status="open",
            priority="high",
            url="https://github.com/org/repo/issues/42",
        )
        assert issue.id == "42"
        assert issue.labels == ["enhancement", "orchestrator"]
        assert issue.url == "https://github.com/org/repo/issues/42"

    def test_serialization(self):
        issue = TrackerIssue(id="1", title="Test", body="Body")
        data = asdict(issue)
        assert data["id"] == "1"
        assert data["title"] == "Test"
        assert isinstance(data["labels"], list)


class TestTrackerProviderProtocol:
    def test_github_provider_is_tracker(self):
        provider = GitHubIssuesProvider()
        assert isinstance(provider, TrackerProvider)

    def test_mock_provider_satisfies_protocol(self):
        mock = MagicMock()
        mock.fetch_issues = MagicMock(return_value=[])
        mock.update_status = MagicMock(return_value=True)
        mock.add_comment = MagicMock(return_value=True)
        # Protocol check is structural, so any object with the right methods works
        assert hasattr(mock, "fetch_issues")
        assert hasattr(mock, "update_status")
        assert hasattr(mock, "add_comment")


class TestCreateTracker:
    def test_create_github(self):
        tracker = create_tracker("github", repo="org/repo")
        assert isinstance(tracker, GitHubIssuesProvider)
        assert tracker.repo == "org/repo"

    def test_create_github_default_repo(self):
        tracker = create_tracker("github")
        assert isinstance(tracker, GitHubIssuesProvider)
        assert tracker.repo == ""

    def test_create_unknown_raises(self):
        with pytest.raises(ValueError, match="Unknown tracker provider"):
            create_tracker("jira")


class TestGitHubIssuesProvider:
    def _fake_run(self, returncode=0, stdout="", stderr=""):
        return MagicMock(returncode=returncode, stdout=stdout, stderr=stderr)

    @patch("lindy_orchestrator.trackers.github_issues.subprocess.run")
    def test_fetch_issues_success(self, mock_run):
        raw_issues = [
            {
                "number": 1,
                "title": "Bug report",
                "body": "Something is broken",
                "labels": [{"name": "bug"}],
                "state": "OPEN",
                "url": "https://github.com/org/repo/issues/1",
            },
            {
                "number": 2,
                "title": "Feature request",
                "body": "Add dark mode",
                "labels": [{"name": "enhancement"}, {"name": "orchestrator"}],
                "state": "OPEN",
                "url": "https://github.com/org/repo/issues/2",
            },
        ]
        mock_run.return_value = self._fake_run(stdout=json.dumps(raw_issues))

        provider = GitHubIssuesProvider(repo="org/repo")
        issues = provider.fetch_issues(project="test")

        assert len(issues) == 2
        assert issues[0].id == "1"
        assert issues[0].title == "Bug report"
        assert issues[0].labels == ["bug"]
        assert issues[1].labels == ["enhancement", "orchestrator"]

    @patch("lindy_orchestrator.trackers.github_issues.subprocess.run")
    def test_fetch_issues_with_labels(self, mock_run):
        mock_run.return_value = self._fake_run(stdout="[]")

        provider = GitHubIssuesProvider(repo="org/repo")
        provider.fetch_issues(project="test", labels=["orchestrator"])

        call_args = mock_run.call_args[0][0]
        assert "--label" in call_args
        assert "orchestrator" in call_args

    @patch("lindy_orchestrator.trackers.github_issues.subprocess.run")
    def test_fetch_issues_empty(self, mock_run):
        mock_run.return_value = self._fake_run(stdout="[]")

        provider = GitHubIssuesProvider()
        issues = provider.fetch_issues(project="test")
        assert issues == []

    @patch("lindy_orchestrator.trackers.github_issues.subprocess.run")
    def test_fetch_issues_gh_failure(self, mock_run):
        mock_run.return_value = self._fake_run(returncode=1, stderr="not authenticated")

        provider = GitHubIssuesProvider()
        issues = provider.fetch_issues(project="test")
        assert issues == []

    @patch("lindy_orchestrator.trackers.github_issues.subprocess.run")
    def test_fetch_issues_invalid_json(self, mock_run):
        mock_run.return_value = self._fake_run(stdout="not json")

        provider = GitHubIssuesProvider()
        issues = provider.fetch_issues(project="test")
        assert issues == []

    @patch("lindy_orchestrator.trackers.github_issues.subprocess.run")
    def test_update_status_close(self, mock_run):
        mock_run.return_value = self._fake_run()

        provider = GitHubIssuesProvider(repo="org/repo")
        result = provider.update_status("42", "closed")

        assert result is True
        call_args = mock_run.call_args[0][0]
        assert "close" in call_args
        assert "42" in call_args

    @patch("lindy_orchestrator.trackers.github_issues.subprocess.run")
    def test_update_status_reopen(self, mock_run):
        mock_run.return_value = self._fake_run()

        provider = GitHubIssuesProvider()
        result = provider.update_status("42", "open")

        assert result is True
        call_args = mock_run.call_args[0][0]
        assert "reopen" in call_args

    @patch("lindy_orchestrator.trackers.github_issues.subprocess.run")
    def test_update_status_with_comment(self, mock_run):
        mock_run.return_value = self._fake_run()

        provider = GitHubIssuesProvider()
        provider.update_status("42", "closed", comment="Done!")

        # Should have called gh twice: close + comment
        assert mock_run.call_count == 2

    @patch("lindy_orchestrator.trackers.github_issues.subprocess.run")
    def test_add_comment(self, mock_run):
        mock_run.return_value = self._fake_run()

        provider = GitHubIssuesProvider(repo="org/repo")
        result = provider.add_comment("42", "Task completed")

        assert result is True
        call_args = mock_run.call_args[0][0]
        assert "comment" in call_args
        assert "42" in call_args
        assert "Task completed" in call_args

    @patch("lindy_orchestrator.trackers.github_issues.subprocess.run")
    def test_add_comment_failure(self, mock_run):
        mock_run.return_value = self._fake_run(returncode=1)

        provider = GitHubIssuesProvider()
        result = provider.add_comment("42", "Comment")
        assert result is False

    @patch("lindy_orchestrator.trackers.github_issues.subprocess.run")
    def test_repo_passed_to_gh(self, mock_run):
        mock_run.return_value = self._fake_run(stdout="[]")

        provider = GitHubIssuesProvider(repo="myorg/myrepo")
        provider.fetch_issues(project="test")

        call_args = mock_run.call_args[0][0]
        assert "--repo" in call_args
        assert "myorg/myrepo" in call_args

    @patch("lindy_orchestrator.trackers.github_issues.subprocess.run")
    def test_no_repo_no_flag(self, mock_run):
        mock_run.return_value = self._fake_run(stdout="[]")

        provider = GitHubIssuesProvider(repo="")
        provider.fetch_issues(project="test")

        call_args = mock_run.call_args[0][0]
        assert "--repo" not in call_args

    @patch("lindy_orchestrator.trackers.github_issues.subprocess.run")
    def test_issue_normalization(self, mock_run):
        """Labels with missing name fields, missing optional fields."""
        raw_issues = [
            {
                "number": 5,
                "title": "Minimal issue",
                "body": "",
                "labels": [{"name": "tag"}, {}],
                "state": "OPEN",
            },
        ]
        mock_run.return_value = self._fake_run(stdout=json.dumps(raw_issues))

        provider = GitHubIssuesProvider()
        issues = provider.fetch_issues(project="test")

        assert len(issues) == 1
        assert issues[0].labels == ["tag", ""]
        assert issues[0].url == ""


class TestTrackerConfig:
    def test_defaults(self):
        cfg = TrackerConfig()
        assert cfg.enabled is False
        assert cfg.provider == "github"
        assert cfg.repo == ""
        assert cfg.labels == ["orchestrator"]
        assert cfg.sync_on_complete is True

    def test_in_orchestrator_config(self):
        cfg = OrchestratorConfig()
        assert cfg.tracker.enabled is False
        assert cfg.tracker.provider == "github"

    def test_custom_config(self):
        cfg = TrackerConfig(
            enabled=True,
            provider="github",
            repo="myorg/myrepo",
            labels=["auto", "orchestrator"],
            sync_on_complete=False,
        )
        assert cfg.enabled is True
        assert cfg.repo == "myorg/myrepo"
        assert cfg.sync_on_complete is False
