"""Tests for CLI extension commands — gc, scan, validate, issues, status."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from lindy_orchestrator.cli import app

runner = CliRunner()


def _write_config(tmp_path, extra=""):
    """Create a minimal config in .orchestrator/config.yaml."""
    orch_dir = tmp_path / ".orchestrator"
    orch_dir.mkdir(parents=True, exist_ok=True)
    cfg = orch_dir / "config.yaml"
    cfg.write_text(
        "project:\n  name: testproject\nmodules:\n  - name: backend\n    path: backend/\n" + extra,
        encoding="utf-8",
    )
    (tmp_path / "backend").mkdir(exist_ok=True)
    return str(cfg)


def _write_status_md(tmp_path):
    """Create a valid status file for the backend module."""
    status_dir = tmp_path / ".orchestrator" / "status"
    status_dir.mkdir(parents=True, exist_ok=True)
    (status_dir / "backend.md").write_text(
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
        "## Blockers\n- (none)\n",
        encoding="utf-8",
    )


def _write_log_file(tmp_path, entries=None):
    """Write sample JSONL log entries."""
    if entries is None:
        entries = [
            '{"timestamp":"2026-01-01T00:00:00","action":"session_start","result":"success","details":{"goal":"test"}}',
            '{"timestamp":"2026-01-01T00:01:00","action":"dispatch","result":"success","details":{"module":"backend"}}',
            '{"timestamp":"2026-01-01T00:02:00","action":"quality_gate","result":"fail","details":{"gate":"pytest"}}',
        ]
    log_dir = tmp_path / ".orchestrator" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "actions.jsonl"
    log_file.write_text("\n".join(entries) + "\n", encoding="utf-8")
    return log_file


class TestValidateCommand:
    def test_validate_valid_config(self, tmp_path):
        cfg_path = _write_config(tmp_path)
        # Create the module status file
        status_dir = tmp_path / ".orchestrator" / "status"
        status_dir.mkdir(parents=True, exist_ok=True)
        (status_dir / "backend.md").write_text(
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
        (tmp_path / ".orchestrator").mkdir(parents=True, exist_ok=True)
        cfg = tmp_path / ".orchestrator" / "config.yaml"
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
        assert call_kwargs[1]["max_session_age_days"] is None

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


class TestStatusCommand:
    """Tests for the unified status command (module health + logs)."""

    def test_status_shows_module_table(self, tmp_path):
        cfg_path = _write_config(tmp_path)
        _write_status_md(tmp_path)
        result = runner.invoke(app, ["status", "-c", cfg_path])
        assert result.exit_code == 0
        assert "backend" in result.output
        assert "GREEN" in result.output

    def test_status_shows_logs(self, tmp_path):
        cfg_path = _write_config(tmp_path)
        _write_status_md(tmp_path)
        _write_log_file(tmp_path)
        result = runner.invoke(app, ["status", "-c", cfg_path])
        assert result.exit_code == 0
        assert "Recent Logs" in result.output
        assert "session_start" in result.output

    def test_status_no_logs_file(self, tmp_path):
        cfg_path = _write_config(tmp_path)
        _write_status_md(tmp_path)
        result = runner.invoke(app, ["status", "-c", cfg_path])
        assert result.exit_code == 0
        assert "No log entries" in result.output

    def test_status_only_flag(self, tmp_path):
        cfg_path = _write_config(tmp_path)
        _write_status_md(tmp_path)
        _write_log_file(tmp_path)
        result = runner.invoke(app, ["status", "-c", cfg_path, "--status-only"])
        assert result.exit_code == 0
        assert "backend" in result.output
        assert "Recent Logs" not in result.output

    def test_logs_only_flag(self, tmp_path):
        cfg_path = _write_config(tmp_path)
        _write_status_md(tmp_path)
        _write_log_file(tmp_path)
        result = runner.invoke(app, ["status", "-c", cfg_path, "--logs-only"])
        assert result.exit_code == 0
        assert "Recent Logs" in result.output
        assert "Module Status Overview" not in result.output

    def test_status_last_n(self, tmp_path):
        cfg_path = _write_config(tmp_path)
        _write_status_md(tmp_path)
        _write_log_file(tmp_path)
        result = runner.invoke(app, ["status", "-c", cfg_path, "--logs-only", "-n", "1"])
        assert result.exit_code == 0
        # Should only show the last entry (quality_gate / fail)
        assert "quality_gate" in result.output
        assert "session_start" not in result.output

    def test_status_json_output(self, tmp_path):
        cfg_path = _write_config(tmp_path)
        _write_status_md(tmp_path)
        _write_log_file(tmp_path)
        result = runner.invoke(app, ["status", "-c", cfg_path, "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "modules" in data
        assert "logs" in data
        assert len(data["modules"]) == 1
        assert data["modules"][0]["name"] == "backend"
        assert len(data["logs"]) == 3

    def test_status_json_status_only(self, tmp_path):
        cfg_path = _write_config(tmp_path)
        _write_status_md(tmp_path)
        _write_log_file(tmp_path)
        result = runner.invoke(app, ["status", "-c", cfg_path, "--json", "--status-only"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "modules" in data
        assert "logs" not in data

    def test_status_json_logs_only(self, tmp_path):
        cfg_path = _write_config(tmp_path)
        _write_log_file(tmp_path)
        result = runner.invoke(app, ["status", "-c", cfg_path, "--json", "--logs-only"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "logs" in data
        assert "modules" not in data

    def test_status_missing_status_md(self, tmp_path):
        cfg_path = _write_config(tmp_path)
        result = runner.invoke(app, ["status", "-c", cfg_path, "--status-only"])
        assert result.exit_code == 0
        # Module should show with "?" health
        assert "backend" in result.output


class TestLogsAlias:
    """Tests for the backward-compat 'logs' command alias."""

    def test_logs_alias_works(self, tmp_path):
        cfg_path = _write_config(tmp_path)
        _write_log_file(tmp_path)
        result = runner.invoke(app, ["logs", "-c", cfg_path])
        assert result.exit_code == 0
        assert "Recent Logs" in result.output
        assert "session_start" in result.output

    def test_logs_alias_json(self, tmp_path):
        cfg_path = _write_config(tmp_path)
        _write_log_file(tmp_path)
        result = runner.invoke(app, ["logs", "-c", cfg_path, "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "logs" in data
        assert "modules" not in data

    def test_logs_alias_with_last(self, tmp_path):
        cfg_path = _write_config(tmp_path)
        _write_log_file(tmp_path)
        result = runner.invoke(app, ["logs", "-c", cfg_path, "-n", "1"])
        assert result.exit_code == 0
        assert "quality_gate" in result.output
        assert "session_start" not in result.output

    def test_logs_alias_no_logs(self, tmp_path):
        cfg_path = _write_config(tmp_path)
        result = runner.invoke(app, ["logs", "-c", cfg_path])
        assert result.exit_code == 0
        assert "No log entries" in result.output
