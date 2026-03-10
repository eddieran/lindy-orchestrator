"""End-to-end CLI tests — extension commands (gc, scan, validate, onboard, logs, issues, edge cases).

Uses Typer CliRunner, mocking only external dependencies (Claude CLI, git, LLM).
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import yaml
from typer.testing import CliRunner

from lindy_orchestrator.cli import app
from lindy_orchestrator.models import DispatchResult

runner = CliRunner()


# ---------------------------------------------------------------------------
# 7. GC command
# ---------------------------------------------------------------------------


class TestE2EGc:
    @patch("lindy_orchestrator.gc.run_gc")
    @patch("lindy_orchestrator.gc.format_gc_report")
    def test_gc_dry_run(self, mock_format, mock_gc, cfg_path):
        mock_report = MagicMock()
        mock_report.actions = []
        mock_report.action_count = 0
        mock_gc.return_value = mock_report
        mock_format.return_value = "Nothing to clean."

        result = runner.invoke(app, ["gc", "-c", cfg_path])
        assert result.exit_code == 0
        assert "DRY RUN" in result.output
        assert "clean" in result.output.lower()
        assert mock_gc.call_args[1]["apply"] is False

    @patch("lindy_orchestrator.gc.run_gc")
    @patch("lindy_orchestrator.gc.format_gc_report")
    def test_gc_apply(self, mock_format, mock_gc, cfg_path):
        mock_report = MagicMock()
        mock_report.actions = ["a"]
        mock_report.action_count = 1
        mock_gc.return_value = mock_report
        mock_format.return_value = "Applied."

        result = runner.invoke(app, ["gc", "-c", cfg_path, "--apply"])
        assert result.exit_code == 0
        assert "APPLY" in result.output
        assert mock_gc.call_args[1]["apply"] is True

    @patch("lindy_orchestrator.gc.run_gc")
    @patch("lindy_orchestrator.gc.format_gc_report")
    def test_gc_with_pending_actions(self, mock_format, mock_gc, cfg_path):
        mock_report = MagicMock()
        mock_report.actions = ["a", "b"]
        mock_report.action_count = 2
        mock_gc.return_value = mock_report
        mock_format.return_value = "Found issues."

        result = runner.invoke(app, ["gc", "-c", cfg_path])
        assert "2 action(s) found" in result.output

    @patch("lindy_orchestrator.gc.run_gc")
    @patch("lindy_orchestrator.gc.format_gc_report")
    def test_gc_custom_thresholds(self, mock_format, mock_gc, cfg_path):
        mock_report = MagicMock()
        mock_report.actions = []
        mock_report.action_count = 0
        mock_gc.return_value = mock_report
        mock_format.return_value = ""

        result = runner.invoke(
            app,
            [
                "gc",
                "-c",
                cfg_path,
                "--branch-age",
                "7",
                "--session-age",
                "15",
                "--log-size",
                "5",
                "--status-stale",
                "3",
            ],
        )
        assert result.exit_code == 0
        kwargs = mock_gc.call_args[1]
        assert kwargs["max_branch_age_days"] == 7
        assert kwargs["max_session_age_days"] == 15
        assert kwargs["max_log_size_mb"] == 5
        assert kwargs["status_stale_days"] == 3


# ---------------------------------------------------------------------------
# 8. Scan command
# ---------------------------------------------------------------------------


class TestE2EScan:
    @patch("lindy_orchestrator.entropy.scanner.run_scan")
    @patch("lindy_orchestrator.entropy.scanner.format_scan_report")
    def test_scan_clean(self, mock_format, mock_scan, cfg_path):
        mock_report = MagicMock()
        mock_report.findings = []
        mock_scan.return_value = mock_report
        mock_format.return_value = "All clear."

        result = runner.invoke(app, ["scan", "-c", cfg_path])
        assert result.exit_code == 0
        assert "No issues found" in result.output

    @patch("lindy_orchestrator.entropy.scanner.run_scan")
    @patch("lindy_orchestrator.entropy.scanner.format_scan_report")
    def test_scan_with_findings(self, mock_format, mock_scan, cfg_path):
        error = MagicMock(severity="error")
        warning = MagicMock(severity="warning")
        mock_report = MagicMock()
        mock_report.findings = [error, warning, warning]
        mock_scan.return_value = mock_report
        mock_format.return_value = "Issues."

        result = runner.invoke(app, ["scan", "-c", cfg_path])
        assert result.exit_code == 0
        assert "1 error(s)" in result.output
        assert "2 warning(s)" in result.output

    @patch("lindy_orchestrator.entropy.scanner.run_scan")
    @patch("lindy_orchestrator.entropy.scanner.format_scan_report")
    def test_scan_warnings_only(self, mock_format, mock_scan, cfg_path):
        warning = MagicMock(severity="warning")
        mock_report = MagicMock()
        mock_report.findings = [warning]
        mock_scan.return_value = mock_report
        mock_format.return_value = "Warnings."

        result = runner.invoke(app, ["scan", "-c", cfg_path])
        assert "1 warning(s)" in result.output
        assert "error" not in result.output.lower()

    @patch("lindy_orchestrator.entropy.scanner.run_scan")
    @patch("lindy_orchestrator.entropy.scanner.format_scan_report")
    def test_scan_module_filter(self, mock_format, mock_scan, cfg_path):
        mock_report = MagicMock()
        mock_report.findings = []
        mock_scan.return_value = mock_report
        mock_format.return_value = ""

        result = runner.invoke(app, ["scan", "-c", cfg_path, "--module", "backend"])
        assert result.exit_code == 0
        mock_scan.assert_called_once()
        assert mock_scan.call_args[1]["module_filter"] == "backend"

    @patch("lindy_orchestrator.entropy.scanner.run_scan")
    @patch("lindy_orchestrator.entropy.scanner.format_scan_report")
    def test_scan_grade_only(self, mock_format, mock_scan, cfg_path):
        mock_report = MagicMock()
        mock_report.findings = []
        mock_scan.return_value = mock_report
        mock_format.return_value = "Grades."

        result = runner.invoke(app, ["scan", "-c", cfg_path, "--grade-only"])
        assert result.exit_code == 0
        mock_format.assert_called_once_with(mock_report, grade_only=True)


# ---------------------------------------------------------------------------
# 9. Validate command
# ---------------------------------------------------------------------------


class TestE2EValidate:
    def test_validate_valid_config(self, cfg_path):
        result = runner.invoke(app, ["validate", "-c", cfg_path])
        assert result.exit_code == 0
        assert "Config valid" in result.output
        assert "All checks passed" in result.output

    def test_validate_missing_config(self, tmp_path):
        result = runner.invoke(app, ["validate", "-c", str(tmp_path / "nope.yaml")])
        assert result.exit_code != 0

    def test_validate_missing_module_path(self, tmp_path):
        config = {
            "project": {"name": "test"},
            "modules": [{"name": "missing_mod", "path": "missing_mod/"}],
        }
        (tmp_path / ".orchestrator").mkdir(parents=True, exist_ok=True)
        (tmp_path / ".orchestrator" / "config.yaml").write_text(yaml.dump(config))
        result = runner.invoke(
            app, ["validate", "-c", str(tmp_path / ".orchestrator" / "config.yaml")]
        )
        assert result.exit_code != 0
        assert "Module path missing" in result.output

    def test_validate_shows_claude_cli_status(self, cfg_path):
        with patch("lindy_orchestrator.cli_ext.find_claude_cli", return_value="/usr/bin/claude"):
            result = runner.invoke(app, ["validate", "-c", cfg_path])
            assert result.exit_code == 0
            assert "Claude CLI found" in result.output

    def test_validate_no_claude_cli(self, cfg_path):
        with patch("lindy_orchestrator.cli_ext.find_claude_cli", return_value=None):
            result = runner.invoke(app, ["validate", "-c", cfg_path])
            assert result.exit_code == 0
            assert "Claude CLI not found" in result.output


# ---------------------------------------------------------------------------
# 10. Onboard command — all three modes
# ---------------------------------------------------------------------------

SAMPLE_LLM_RESPONSE = {
    "project_name": "test-project",
    "project_description": "Test",
    "modules": [
        {
            "name": "api",
            "path": "api",
            "tech_stack": ["Python"],
            "test_commands": ["pytest"],
            "build_commands": [],
            "lint_commands": [],
        }
    ],
    "cross_deps": [],
    "coordination_complexity": 1,
    "branch_prefix": "af",
    "sensitive_paths": [],
    "qa_requirements": {},
    "monorepo": False,
}


class TestE2EOnboard:
    def test_onboard_empty_project_no_description(self, tmp_path, monkeypatch):
        """Empty project without description should error."""
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, ["onboard", "-y"])
        assert result.exit_code != 0

    def test_onboard_scaffold_mode(self, tmp_path, monkeypatch):
        """Empty project with description triggers scaffold mode."""
        monkeypatch.chdir(tmp_path)

        def mock_dispatch(module, working_dir, prompt):
            return DispatchResult(
                module=module,
                success=True,
                output=json.dumps(SAMPLE_LLM_RESPONSE),
            )

        with (
            patch("shutil.which", return_value="/usr/bin/claude"),
            patch("lindy_orchestrator.cli_onboard.create_provider") as mock_pf,
        ):
            mock_pf.return_value.dispatch_simple.side_effect = mock_dispatch
            result = runner.invoke(app, ["onboard", "A Python API project", "-y"])
            assert result.exit_code == 0
            assert "scaffold mode" in result.output.lower()
            assert "Onboarding complete" in result.output
            assert (tmp_path / ".orchestrator" / "config.yaml").exists()

    def test_onboard_init_mode(self, tmp_path, monkeypatch):
        """Existing project without config triggers init+onboard mode."""
        monkeypatch.chdir(tmp_path)
        backend = tmp_path / "backend"
        backend.mkdir()
        (backend / "pyproject.toml").write_text('[project]\nname = "test"')

        result = runner.invoke(app, ["onboard", "-y"])
        assert "init+onboard" in result.output.lower() or result.exit_code == 0

    def test_onboard_re_onboard_mode(self, tmp_path, monkeypatch):
        """Project with existing config triggers re-onboard mode."""
        monkeypatch.chdir(tmp_path)
        config = {
            "project": {"name": "existing"},
            "modules": [{"name": "app", "path": "app/"}],
        }
        (tmp_path / ".orchestrator").mkdir(parents=True, exist_ok=True)
        (tmp_path / ".orchestrator" / "config.yaml").write_text(yaml.dump(config))
        (tmp_path / "app").mkdir()
        (tmp_path / "app" / "pyproject.toml").write_text('[project]\nname = "app"')

        result = runner.invoke(app, ["onboard", "-y"])
        assert "re-onboard" in result.output.lower()

    def test_onboard_scaffold_no_claude_cli(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        with patch("shutil.which", return_value=None):
            result = runner.invoke(app, ["onboard", "A project", "-y"])
            assert result.exit_code != 0
            assert "Claude CLI" in result.output

    def test_onboard_scaffold_from_file(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        desc_file = tmp_path / "desc.md"
        desc_file.write_text("A microservice project")

        def mock_dispatch(module, working_dir, prompt):
            return DispatchResult(
                module=module,
                success=True,
                output=json.dumps(SAMPLE_LLM_RESPONSE),
            )

        with (
            patch("shutil.which", return_value="/usr/bin/claude"),
            patch("lindy_orchestrator.cli_onboard.create_provider") as mock_pf,
        ):
            mock_pf.return_value.dispatch_simple.side_effect = mock_dispatch
            result = runner.invoke(app, ["onboard", "--file", str(desc_file), "-y"])
            assert result.exit_code == 0
            assert "Onboarding complete" in result.output


# ---------------------------------------------------------------------------
# 11. Logs alias (backward compat)
# ---------------------------------------------------------------------------


class TestE2ELogsAlias:
    def test_logs_shows_entries(self, project_with_logs, cfg_path):
        result = runner.invoke(app, ["logs", "-c", cfg_path])
        assert result.exit_code == 0
        assert "Recent Logs" in result.output

    def test_logs_json(self, project_with_logs, cfg_path):
        result = runner.invoke(app, ["logs", "-c", cfg_path, "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "logs" in data

    def test_logs_no_entries(self, cfg_path):
        result = runner.invoke(app, ["logs", "-c", cfg_path])
        assert result.exit_code == 0
        assert "No log entries" in result.output


# ---------------------------------------------------------------------------
# 12. Edge cases
# ---------------------------------------------------------------------------


class TestE2EEdgeCases:
    def test_no_config_file(self, tmp_path):
        """Commands requiring config fail gracefully when no config exists."""
        result = runner.invoke(app, ["status", "-c", str(tmp_path / "nope.yaml")])
        assert result.exit_code != 0

    def test_invalid_config_yaml(self, tmp_path):
        """Malformed YAML should fail gracefully."""
        (tmp_path / ".orchestrator").mkdir(parents=True, exist_ok=True)
        bad = tmp_path / ".orchestrator" / "config.yaml"
        bad.write_text("invalid: yaml: [broken")
        result = runner.invoke(app, ["status", "-c", str(bad)])
        assert result.exit_code != 0

    def test_empty_project_dir_validate(self, tmp_path):
        """Validate on an empty dir with no config should fail."""
        result = runner.invoke(
            app, ["validate", "-c", str(tmp_path / ".orchestrator" / "config.yaml")]
        )
        assert result.exit_code != 0

    def test_no_args_shows_help(self):
        """Invoking without args/commands shows help text."""
        result = runner.invoke(app, [])
        assert "Usage" in result.output or "lindy-orchestrate" in result.output

    def test_help_flag(self):
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "run" in result.output
        assert "status" in result.output
        assert "onboard" in result.output

    def test_unknown_command(self):
        result = runner.invoke(app, ["nonexistent-command"])
        assert result.exit_code != 0

    def test_config_with_no_modules(self, tmp_path):
        """Config with empty modules list — status still works."""
        config = {"project": {"name": "empty"}, "modules": []}
        (tmp_path / ".orchestrator").mkdir(parents=True, exist_ok=True)
        (tmp_path / ".orchestrator" / "config.yaml").write_text(yaml.dump(config))
        result = runner.invoke(
            app, ["status", "-c", str(tmp_path / ".orchestrator" / "config.yaml"), "--status-only"]
        )
        assert result.exit_code == 0

    def test_resume_with_no_plan_json(self, project_dir, cfg_path):
        """Resume with session that has no plan_json shows re-run message."""
        from lindy_orchestrator.session import SessionManager

        sessions = SessionManager(project_dir / ".orchestrator" / "sessions")
        session = sessions.create(goal="No plan session")
        session.status = "paused"
        session.plan_json = None
        sessions.save(session)

        result = runner.invoke(app, ["resume", "-c", cfg_path])
        assert "No saved plan found" in result.output

    def test_mailbox_priority_message(self, project_dir, cfg_path):
        """Send a high-priority message and verify it appears."""
        result = runner.invoke(
            app,
            [
                "mailbox",
                "--send-to",
                "backend",
                "-m",
                "urgent fix",
                "--priority",
                "high",
                "-c",
                cfg_path,
            ],
        )
        assert result.exit_code == 0
        result = runner.invoke(app, ["mailbox", "backend", "-c", cfg_path])
        assert "urgent fix" in result.output


# ---------------------------------------------------------------------------
# 13. Issues command
# ---------------------------------------------------------------------------


class TestE2EIssues:
    def test_issues_tracker_disabled(self, cfg_path):
        result = runner.invoke(app, ["issues", "-c", cfg_path])
        assert result.exit_code == 0
        assert "disabled" in result.output.lower()

    @patch("lindy_orchestrator.trackers.create_tracker")
    def test_issues_with_results(self, mock_create, tmp_path):
        from lindy_orchestrator.trackers.base import TrackerIssue

        config = {
            "project": {"name": "test"},
            "modules": [{"name": "x", "path": "x/"}],
            "tracker": {"enabled": True, "repo": "org/repo"},
        }
        (tmp_path / ".orchestrator").mkdir(parents=True, exist_ok=True)
        (tmp_path / ".orchestrator" / "config.yaml").write_text(yaml.dump(config))
        (tmp_path / "x").mkdir()

        mock_tracker = MagicMock()
        mock_tracker.fetch_issues.return_value = [
            TrackerIssue(id="42", title="Fix bug", body="Details"),
        ]
        mock_create.return_value = mock_tracker

        result = runner.invoke(
            app, ["issues", "-c", str(tmp_path / ".orchestrator" / "config.yaml")]
        )
        assert result.exit_code == 0
        assert "#42" in result.output
        assert "Fix bug" in result.output

    @patch("lindy_orchestrator.trackers.create_tracker")
    def test_issues_json(self, mock_create, tmp_path):
        from lindy_orchestrator.trackers.base import TrackerIssue

        config = {
            "project": {"name": "test"},
            "modules": [{"name": "x", "path": "x/"}],
            "tracker": {"enabled": True, "repo": "org/repo"},
        }
        (tmp_path / ".orchestrator").mkdir(parents=True, exist_ok=True)
        (tmp_path / ".orchestrator" / "config.yaml").write_text(yaml.dump(config))
        (tmp_path / "x").mkdir()

        mock_tracker = MagicMock()
        mock_tracker.fetch_issues.return_value = [
            TrackerIssue(id="1", title="Issue", body="body"),
        ]
        mock_create.return_value = mock_tracker

        result = runner.invoke(
            app, ["issues", "-c", str(tmp_path / ".orchestrator" / "config.yaml"), "--json"]
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert len(data) == 1
