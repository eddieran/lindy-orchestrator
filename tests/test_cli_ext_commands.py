"""Tests for CLI extension commands — gc, scan, validate, issues."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from lindy_orchestrator.cli import app

runner = CliRunner()


def _write_config(tmp_path, extra=""):
    """Create a minimal orchestrator.yaml in tmp_path."""
    cfg = tmp_path / "orchestrator.yaml"
    cfg.write_text(
        "project:\n  name: testproject\nmodules:\n  - name: backend\n    path: backend/\n" + extra,
        encoding="utf-8",
    )
    (tmp_path / "backend").mkdir(exist_ok=True)
    return str(cfg)


class TestValidateCommand:
    def test_validate_valid_config(self, tmp_path):
        cfg_path = _write_config(tmp_path)
        # Create the module directory and STATUS.md
        (tmp_path / "backend" / "STATUS.md").write_text(
            "# Backend Status\n\n"
            "## Meta\n"
            "| Key | Value |\n"
            "|-----|-------|\n"
            "| module | backend |\n"
            "| last_updated | 2026-01-01 |\n"
            "| overall_health | GREEN |\n"
            "| agent_session | — |\n\n"
            "## Active Work\n"
            "| ID | Task | Status | BlockedBy | Started | Notes |\n"
            "|----|------|--------|-----------|---------|-------|\n\n"
            "## Completed (Recent)\n| ID | Task | Completed | Outcome |\n|----|------|-----------|--------|\n\n"
            "## Backlog\n- (none)\n\n"
            "## Cross-Module Requests\n"
            "| ID | From | To | Request | Priority | Status |\n"
            "|----|------|----|---------|----------|--------|\n\n"
            "## Cross-Module Deliverables\n"
            "| ID | From | To | Deliverable | Status | Path |\n"
            "|----|------|----|-------------|--------|------|\n\n"
            "## Key Metrics\n| Metric | Value |\n|--------|-------|\n\n"
            "## Blockers\n- (none)\n"
        )
        result = runner.invoke(app, ["validate", "-c", cfg_path])
        assert result.exit_code == 0
        assert "Config valid" in result.output

    def test_validate_missing_config(self, tmp_path):
        result = runner.invoke(app, ["validate", "-c", str(tmp_path / "nope.yaml")])
        assert result.exit_code != 0

    def test_validate_missing_module_path(self, tmp_path):
        cfg = tmp_path / "orchestrator.yaml"
        cfg.write_text(
            "project:\n  name: test\nmodules:\n  - name: missing_mod\n    path: missing_mod/\n"
        )
        result = runner.invoke(app, ["validate", "-c", str(cfg)])
        assert result.exit_code != 0
        assert "Module path missing" in result.output


class TestGcCommand:
    @patch("lindy_orchestrator.gc.run_gc")
    @patch("lindy_orchestrator.gc.format_gc_report")
    def test_gc_dry_run(self, mock_format, mock_run_gc, tmp_path):
        cfg_path = _write_config(tmp_path)
        mock_report = MagicMock()
        mock_report.actions = []
        mock_report.action_count = 0
        mock_run_gc.return_value = mock_report
        mock_format.return_value = "No actions."

        result = runner.invoke(app, ["gc", "-c", cfg_path])
        assert result.exit_code == 0
        assert "DRY RUN" in result.output
        assert mock_run_gc.called
        # Verify apply=False
        call_kwargs = mock_run_gc.call_args
        assert call_kwargs[1]["apply"] is False

    @patch("lindy_orchestrator.gc.run_gc")
    @patch("lindy_orchestrator.gc.format_gc_report")
    def test_gc_apply_mode(self, mock_format, mock_run_gc, tmp_path):
        cfg_path = _write_config(tmp_path)
        mock_report = MagicMock()
        mock_report.actions = ["action1"]
        mock_report.action_count = 1
        mock_run_gc.return_value = mock_report
        mock_format.return_value = "1 action applied."

        result = runner.invoke(app, ["gc", "-c", cfg_path, "--apply"])
        assert result.exit_code == 0
        assert "APPLY" in result.output
        call_kwargs = mock_run_gc.call_args
        assert call_kwargs[1]["apply"] is True

    @patch("lindy_orchestrator.gc.run_gc")
    @patch("lindy_orchestrator.gc.format_gc_report")
    def test_gc_clean_workspace(self, mock_format, mock_run_gc, tmp_path):
        cfg_path = _write_config(tmp_path)
        mock_report = MagicMock()
        mock_report.actions = []
        mock_report.action_count = 0
        mock_run_gc.return_value = mock_report
        mock_format.return_value = ""

        result = runner.invoke(app, ["gc", "-c", cfg_path])
        assert "clean" in result.output.lower()

    @patch("lindy_orchestrator.gc.run_gc")
    @patch("lindy_orchestrator.gc.format_gc_report")
    def test_gc_pending_actions(self, mock_format, mock_run_gc, tmp_path):
        cfg_path = _write_config(tmp_path)
        mock_report = MagicMock()
        mock_report.actions = ["a", "b", "c"]
        mock_report.action_count = 3
        mock_run_gc.return_value = mock_report
        mock_format.return_value = "3 actions"

        result = runner.invoke(app, ["gc", "-c", cfg_path])
        assert "3 action(s) found" in result.output


class TestScanCommand:
    @patch("lindy_orchestrator.entropy.scanner.run_scan")
    @patch("lindy_orchestrator.entropy.scanner.format_scan_report")
    def test_scan_no_issues(self, mock_format, mock_scan, tmp_path):
        cfg_path = _write_config(tmp_path)
        mock_report = MagicMock()
        mock_report.findings = []
        mock_scan.return_value = mock_report
        mock_format.return_value = "Clean."

        result = runner.invoke(app, ["scan", "-c", cfg_path])
        assert result.exit_code == 0
        assert "No issues found" in result.output

    @patch("lindy_orchestrator.entropy.scanner.run_scan")
    @patch("lindy_orchestrator.entropy.scanner.format_scan_report")
    def test_scan_with_errors(self, mock_format, mock_scan, tmp_path):
        cfg_path = _write_config(tmp_path)
        error = MagicMock()
        error.severity = "error"
        warning = MagicMock()
        warning.severity = "warning"
        mock_report = MagicMock()
        mock_report.findings = [error, warning, warning]
        mock_scan.return_value = mock_report
        mock_format.return_value = "Issues found."

        result = runner.invoke(app, ["scan", "-c", cfg_path])
        assert result.exit_code == 0
        assert "1 error(s)" in result.output
        assert "2 warning(s)" in result.output


class TestIssuesCommand:
    def test_issues_tracker_disabled(self, tmp_path):
        cfg_path = _write_config(tmp_path)
        result = runner.invoke(app, ["issues", "-c", cfg_path])
        assert result.exit_code == 0
        assert "disabled" in result.output.lower()

    @patch("lindy_orchestrator.trackers.create_tracker")
    def test_issues_no_results(self, mock_create, tmp_path):
        cfg_path = _write_config(tmp_path, "tracker:\n  enabled: true\n  repo: org/repo\n")
        mock_tracker = MagicMock()
        mock_tracker.fetch_issues.return_value = []
        mock_create.return_value = mock_tracker

        result = runner.invoke(app, ["issues", "-c", cfg_path])
        assert result.exit_code == 0
        assert "No issues found" in result.output

    @patch("lindy_orchestrator.trackers.create_tracker")
    def test_issues_with_results(self, mock_create, tmp_path):
        from lindy_orchestrator.trackers.base import TrackerIssue

        cfg_path = _write_config(tmp_path, "tracker:\n  enabled: true\n  repo: org/repo\n")
        mock_tracker = MagicMock()
        mock_tracker.fetch_issues.return_value = [
            TrackerIssue(id="1", title="Bug fix", body="Fix it", labels=["bug"]),
            TrackerIssue(id="2", title="Feature", body="Add it"),
        ]
        mock_create.return_value = mock_tracker

        result = runner.invoke(app, ["issues", "-c", cfg_path])
        assert result.exit_code == 0
        assert "2 issue(s)" in result.output
        assert "#1" in result.output
        assert "Bug fix" in result.output

    @patch("lindy_orchestrator.trackers.create_tracker")
    def test_issues_fetch_failure(self, mock_create, tmp_path):
        cfg_path = _write_config(tmp_path, "tracker:\n  enabled: true\n  repo: org/repo\n")
        mock_tracker = MagicMock()
        mock_tracker.fetch_issues.side_effect = RuntimeError("network error")
        mock_create.return_value = mock_tracker

        result = runner.invoke(app, ["issues", "-c", cfg_path])
        assert result.exit_code != 0
        assert "Failed to fetch" in result.output

    @patch("lindy_orchestrator.trackers.create_tracker")
    def test_issues_json_output(self, mock_create, tmp_path):
        from lindy_orchestrator.trackers.base import TrackerIssue

        cfg_path = _write_config(tmp_path, "tracker:\n  enabled: true\n  repo: org/repo\n")
        mock_tracker = MagicMock()
        mock_tracker.fetch_issues.return_value = [
            TrackerIssue(id="1", title="Bug", body="Fix"),
        ]
        mock_create.return_value = mock_tracker

        result = runner.invoke(app, ["issues", "-c", cfg_path, "--json"])
        assert result.exit_code == 0
        # Output should be valid JSON
        data = json.loads(result.output)
        assert len(data) == 1
        assert data[0]["id"] == "1"
