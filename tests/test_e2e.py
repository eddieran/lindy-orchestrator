"""End-to-end CLI tests via Typer CliRunner.

Covers every registered CLI command through the full CLI flow,
mocking only external dependencies (Claude CLI, git, LLM providers).
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml
from typer.testing import CliRunner

from lindy_orchestrator import __version__
from lindy_orchestrator.cli import app
from lindy_orchestrator.mailbox import Mailbox, Message
from lindy_orchestrator.models import DispatchResult, TaskItem, TaskPlan, TaskStatus

runner = CliRunner()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

MINIMAL_STATUS_MD = (
    "# Status\n\n"
    "## Meta\n"
    "| Key | Value |\n|-----|-------|\n"
    "| module | {name} |\n"
    "| last_updated | 2026-01-01 |\n"
    "| overall_health | GREEN |\n"
    "| agent_session | — |\n\n"
    "## Active Work\n"
    "| ID | Task | Status | BlockedBy | Started | Notes |\n"
    "|----|------|--------|-----------|---------|-------|\n\n"
    "## Completed (Recent)\n| ID | Task | Completed | Outcome |\n"
    "|----|------|-----------|--------|\n\n"
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


@pytest.fixture()
def project_dir(tmp_path: Path) -> Path:
    """Create a minimal orchestrator project with config, modules, logs, sessions."""
    config = {
        "project": {"name": "e2e-project", "branch_prefix": "af"},
        "modules": [
            {"name": "backend", "path": "backend/"},
            {"name": "frontend", "path": "frontend/"},
        ],
        "mailbox": {"enabled": True, "dir": ".orchestrator/mailbox"},
    }
    (tmp_path / "orchestrator.yaml").write_text(yaml.dump(config))

    for mod in ("backend", "frontend"):
        mod_dir = tmp_path / mod
        mod_dir.mkdir()
        (mod_dir / "STATUS.md").write_text(MINIMAL_STATUS_MD.format(name=mod))

    (tmp_path / ".orchestrator" / "mailbox").mkdir(parents=True)
    (tmp_path / ".orchestrator" / "logs").mkdir(parents=True)
    (tmp_path / ".orchestrator" / "sessions").mkdir(parents=True)
    return tmp_path


@pytest.fixture()
def cfg_path(project_dir: Path) -> str:
    return str(project_dir / "orchestrator.yaml")


@pytest.fixture()
def project_with_logs(project_dir: Path) -> Path:
    """Project dir with sample JSONL log entries."""
    log_file = project_dir / ".orchestrator" / "logs" / "actions.jsonl"
    entries = [
        '{"timestamp":"2026-01-01T00:00:00","action":"session_start","result":"success","details":{"goal":"test"}}',
        '{"timestamp":"2026-01-01T00:01:00","action":"dispatch","result":"success","details":{"module":"backend"}}',
        '{"timestamp":"2026-01-01T00:02:00","action":"quality_gate","result":"fail","details":{"gate":"pytest"}}',
    ]
    log_file.write_text("\n".join(entries) + "\n")
    return project_dir


def _make_plan(goal: str = "Test goal") -> TaskPlan:
    """Create a simple two-task plan for testing."""
    return TaskPlan(
        goal=goal,
        tasks=[
            TaskItem(
                id=1,
                module="backend",
                description="Setup API",
                status=TaskStatus.COMPLETED,
                result="done",
            ),
            TaskItem(
                id=2,
                module="frontend",
                description="Build UI",
                depends_on=[1],
                status=TaskStatus.PENDING,
            ),
        ],
    )


def _mock_generate_plan(goal, cfg, on_progress=None, progress=None):
    """Mock planner.generate_plan to return a simple plan."""
    plan = _make_plan(goal)
    for t in plan.tasks:
        t.status = TaskStatus.PENDING
    return plan


def _mock_execute_plan(plan, cfg, logger, on_progress=None, verbose=False, hooks=None):
    """Mock scheduler.execute_plan — marks all pending tasks completed."""
    for t in plan.tasks:
        if t.status == TaskStatus.PENDING:
            t.status = TaskStatus.COMPLETED
            t.result = "mocked success"
    return plan


# ---------------------------------------------------------------------------
# 1. Version command
# ---------------------------------------------------------------------------


class TestE2EVersion:
    def test_version_flag(self):
        result = runner.invoke(app, ["--version"])
        assert result.exit_code == 0
        assert __version__ in result.output

    def test_version_command(self):
        result = runner.invoke(app, ["version"])
        assert result.exit_code == 0
        assert __version__ in result.output

    def test_version_json(self):
        result = runner.invoke(app, ["version", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["version"] == __version__


# ---------------------------------------------------------------------------
# 2. Status command — unified with logs
# ---------------------------------------------------------------------------


class TestE2EStatus:
    def test_status_shows_modules_and_health(self, cfg_path):
        result = runner.invoke(app, ["status", "-c", cfg_path])
        assert result.exit_code == 0
        assert "backend" in result.output
        assert "frontend" in result.output
        assert "GREEN" in result.output

    def test_status_json(self, project_with_logs, cfg_path):
        result = runner.invoke(app, ["status", "-c", cfg_path, "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "modules" in data
        assert "logs" in data
        assert len(data["modules"]) == 2

    def test_status_logs_only(self, project_with_logs, cfg_path):
        result = runner.invoke(app, ["status", "-c", cfg_path, "--logs-only"])
        assert result.exit_code == 0
        assert "Recent Logs" in result.output
        assert "session_start" in result.output

    def test_status_status_only(self, project_with_logs, cfg_path):
        result = runner.invoke(app, ["status", "-c", cfg_path, "--status-only"])
        assert result.exit_code == 0
        assert "backend" in result.output
        # Should NOT show logs section header
        assert "Recent Logs" not in result.output

    def test_status_json_status_only(self, project_with_logs, cfg_path):
        result = runner.invoke(app, ["status", "-c", cfg_path, "--json", "--status-only"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "modules" in data
        assert "logs" not in data

    def test_status_json_logs_only(self, project_with_logs, cfg_path):
        result = runner.invoke(app, ["status", "-c", cfg_path, "--json", "--logs-only"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "logs" in data
        assert "modules" not in data

    def test_status_no_log_file(self, cfg_path):
        result = runner.invoke(app, ["status", "-c", cfg_path])
        assert result.exit_code == 0
        assert "No log entries" in result.output

    def test_status_last_n(self, project_with_logs, cfg_path):
        result = runner.invoke(app, ["status", "-c", cfg_path, "--logs-only", "-n", "1"])
        assert result.exit_code == 0
        assert "quality_gate" in result.output
        assert "session_start" not in result.output

    def test_status_missing_status_md(self, tmp_path):
        """Module exists but STATUS.md is absent — shows '?' health."""
        config = {
            "project": {"name": "test"},
            "modules": [{"name": "api", "path": "api/"}],
        }
        (tmp_path / "orchestrator.yaml").write_text(yaml.dump(config))
        (tmp_path / "api").mkdir()

        result = runner.invoke(
            app, ["status", "-c", str(tmp_path / "orchestrator.yaml"), "--status-only"]
        )
        assert result.exit_code == 0
        assert "api" in result.output

    def test_status_shows_mailbox_summary(self, project_dir, cfg_path):
        mb = Mailbox(project_dir / ".orchestrator" / "mailbox")
        mb.send(Message(from_module="frontend", to_module="backend", content="ping"))
        result = runner.invoke(app, ["status", "-c", cfg_path, "--status-only"])
        assert result.exit_code == 0
        assert "Mailbox" in result.output
        assert "1 pending" in result.output

    def test_status_json_includes_mailbox(self, project_dir, cfg_path):
        result = runner.invoke(app, ["status", "-c", cfg_path, "--json", "--status-only"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "mailbox" in data


# ---------------------------------------------------------------------------
# 3. Mailbox command
# ---------------------------------------------------------------------------


class TestE2EMailbox:
    def test_mailbox_summary_empty(self, cfg_path):
        result = runner.invoke(app, ["mailbox", "-c", cfg_path])
        assert result.exit_code == 0
        assert "Mailbox Summary" in result.output

    def test_mailbox_send_and_receive(self, project_dir, cfg_path):
        # Send
        result = runner.invoke(
            app,
            [
                "mailbox",
                "--send-to",
                "backend",
                "--send-from",
                "frontend",
                "-m",
                "API ready?",
                "-c",
                cfg_path,
            ],
        )
        assert result.exit_code == 0
        assert "Sent" in result.output

        # Receive
        result = runner.invoke(app, ["mailbox", "backend", "-c", cfg_path])
        assert result.exit_code == 0
        assert "API ready?" in result.output
        assert "frontend" in result.output

    def test_mailbox_send_default_from(self, project_dir, cfg_path):
        result = runner.invoke(
            app,
            [
                "mailbox",
                "--send-to",
                "backend",
                "-m",
                "hello",
                "-c",
                cfg_path,
            ],
        )
        assert result.exit_code == 0
        mb = Mailbox(project_dir / ".orchestrator" / "mailbox")
        msgs = mb.receive("backend")
        assert msgs[0].from_module == "cli"

    def test_mailbox_send_requires_message(self, cfg_path):
        result = runner.invoke(
            app,
            [
                "mailbox",
                "--send-to",
                "backend",
                "-c",
                cfg_path,
            ],
        )
        assert result.exit_code == 1

    def test_mailbox_view_empty_module(self, cfg_path):
        result = runner.invoke(app, ["mailbox", "frontend", "-c", cfg_path])
        assert result.exit_code == 0
        assert "no pending" in result.output.lower()

    def test_mailbox_view_json(self, project_dir, cfg_path):
        mb = Mailbox(project_dir / ".orchestrator" / "mailbox")
        mb.send(Message(from_module="a", to_module="backend", content="json test"))
        result = runner.invoke(app, ["mailbox", "backend", "--json", "-c", cfg_path])
        assert result.exit_code == 0
        assert "json test" in result.output

    def test_mailbox_disabled(self, tmp_path):
        config = {
            "project": {"name": "test"},
            "modules": [{"name": "x", "path": "x/"}],
            "mailbox": {"enabled": False},
        }
        (tmp_path / "orchestrator.yaml").write_text(yaml.dump(config))
        (tmp_path / "x").mkdir()
        result = runner.invoke(app, ["mailbox", "-c", str(tmp_path / "orchestrator.yaml")])
        assert "disabled" in result.output.lower()

    def test_mailbox_summary_with_messages(self, project_dir, cfg_path):
        mb = Mailbox(project_dir / ".orchestrator" / "mailbox")
        mb.send(Message(from_module="a", to_module="backend", content="msg1"))
        mb.send(Message(from_module="b", to_module="backend", content="msg2"))
        result = runner.invoke(app, ["mailbox", "-c", cfg_path])
        assert result.exit_code == 0
        assert "2 pending" in result.output


# ---------------------------------------------------------------------------
# 4. Run command — dry-run with dashboard rendering
# ---------------------------------------------------------------------------


class TestE2ERun:
    @patch("lindy_orchestrator.cli.find_claude_cli", return_value="/usr/bin/claude")
    @patch("lindy_orchestrator.scheduler.execute_plan", side_effect=_mock_execute_plan)
    @patch("lindy_orchestrator.planner.generate_plan", side_effect=_mock_generate_plan)
    def test_run_dry_run(self, mock_plan, mock_exec, mock_cli, cfg_path):
        result = runner.invoke(
            app,
            [
                "run",
                "Build a feature",
                "--dry-run",
                "-c",
                cfg_path,
            ],
        )
        assert result.exit_code == 0
        assert "tasks planned" in result.output
        # Dry-run flag should be propagated
        call_args = mock_exec.call_args
        assert call_args[0][1].safety.dry_run is True

    @patch("lindy_orchestrator.cli.find_claude_cli", return_value="/usr/bin/claude")
    @patch("lindy_orchestrator.scheduler.execute_plan", side_effect=_mock_execute_plan)
    @patch("lindy_orchestrator.planner.generate_plan", side_effect=_mock_generate_plan)
    def test_run_full_flow(self, mock_plan, mock_exec, mock_cli, cfg_path):
        result = runner.invoke(
            app,
            [
                "run",
                "Implement auth",
                "-c",
                cfg_path,
            ],
        )
        assert result.exit_code == 0
        assert "GOAL COMPLETED" in result.output

    @patch("lindy_orchestrator.cli.find_claude_cli", return_value=None)
    def test_run_no_claude_cli(self, mock_cli, cfg_path):
        result = runner.invoke(app, ["run", "test goal", "-c", cfg_path])
        assert result.exit_code != 0
        assert "Claude CLI not found" in result.output

    def test_run_no_goal(self, cfg_path):
        result = runner.invoke(app, ["run", "-c", cfg_path])
        # Should fail because no goal provided
        assert result.exit_code != 0

    @patch("lindy_orchestrator.cli.find_claude_cli", return_value="/usr/bin/claude")
    @patch("lindy_orchestrator.scheduler.execute_plan", side_effect=_mock_execute_plan)
    def test_run_from_plan_file(self, mock_exec, mock_cli, project_dir, cfg_path):
        """Run from a saved plan JSON (skip planning step)."""
        from lindy_orchestrator.models import plan_to_dict

        plan = _make_plan("Plan file goal")
        for t in plan.tasks:
            t.status = TaskStatus.PENDING
        plan_file = project_dir / "plan.json"
        plan_file.write_text(json.dumps(plan_to_dict(plan), indent=2, default=str))

        result = runner.invoke(
            app,
            [
                "run",
                "--plan",
                str(plan_file),
                "-c",
                cfg_path,
            ],
        )
        assert result.exit_code == 0
        assert "Loaded plan from" in result.output

    @patch("lindy_orchestrator.cli.find_claude_cli", return_value="/usr/bin/claude")
    def test_run_plan_file_not_found(self, mock_cli, cfg_path):
        result = runner.invoke(
            app,
            [
                "run",
                "--plan",
                "/nonexistent/plan.json",
                "-c",
                cfg_path,
            ],
        )
        assert result.exit_code != 0
        assert "not found" in result.output.lower()

    @patch("lindy_orchestrator.cli.find_claude_cli", return_value="/usr/bin/claude")
    @patch("lindy_orchestrator.planner.generate_plan", side_effect=RuntimeError("LLM down"))
    def test_run_planner_failure(self, mock_plan, mock_cli, cfg_path):
        result = runner.invoke(app, ["run", "test", "-c", cfg_path])
        assert result.exit_code != 0
        assert "failed" in result.output.lower()

    @patch("lindy_orchestrator.cli.find_claude_cli", return_value="/usr/bin/claude")
    @patch("lindy_orchestrator.scheduler.execute_plan")
    @patch("lindy_orchestrator.planner.generate_plan", side_effect=_mock_generate_plan)
    def test_run_with_failures(self, mock_plan, mock_exec, mock_cli, cfg_path):
        """When some tasks fail, report shows PAUSED."""

        def exec_with_failure(plan, cfg, logger, on_progress=None, verbose=False, hooks=None):
            plan.tasks[0].status = TaskStatus.COMPLETED
            plan.tasks[1].status = TaskStatus.FAILED
            plan.tasks[1].result = "error occurred"
            return plan

        mock_exec.side_effect = exec_with_failure
        result = runner.invoke(app, ["run", "test", "-c", cfg_path])
        assert result.exit_code == 0
        assert "PAUSED" in result.output


# ---------------------------------------------------------------------------
# 5. Plan command
# ---------------------------------------------------------------------------


class TestE2EPlan:
    @patch("lindy_orchestrator.planner.generate_plan", side_effect=_mock_generate_plan)
    def test_plan_basic(self, mock_plan, cfg_path):
        result = runner.invoke(app, ["plan", "Build an API", "-c", cfg_path])
        assert result.exit_code == 0
        assert "2 tasks" in result.output
        assert "Plan saved to" in result.output

    @patch("lindy_orchestrator.planner.generate_plan", side_effect=_mock_generate_plan)
    def test_plan_with_output_file(self, mock_plan, project_dir, cfg_path):
        out_file = project_dir / "myplan.json"
        result = runner.invoke(
            app,
            [
                "plan",
                "Build an API",
                "-c",
                cfg_path,
                "-o",
                str(out_file),
            ],
        )
        assert result.exit_code == 0
        assert out_file.exists()
        data = json.loads(out_file.read_text())
        assert data["goal"] == "Build an API"
        assert len(data["tasks"]) == 2

    def test_plan_no_goal(self, cfg_path):
        result = runner.invoke(app, ["plan", "-c", cfg_path])
        assert result.exit_code != 0

    @patch("lindy_orchestrator.planner.generate_plan", side_effect=_mock_generate_plan)
    def test_plan_from_file(self, mock_plan, project_dir, cfg_path):
        goal_file = project_dir / "goal.md"
        goal_file.write_text("Implement feature X with tests")
        result = runner.invoke(
            app,
            [
                "plan",
                "--file",
                str(goal_file),
                "-c",
                cfg_path,
            ],
        )
        assert result.exit_code == 0
        assert "2 tasks" in result.output


# ---------------------------------------------------------------------------
# 6. Resume command
# ---------------------------------------------------------------------------


class TestE2EResume:
    @patch("lindy_orchestrator.scheduler.execute_plan", side_effect=_mock_execute_plan)
    def test_resume_latest_session(self, mock_exec, project_dir, cfg_path):
        """Resume picks up the latest session and re-executes pending tasks."""
        from lindy_orchestrator.models import plan_to_dict
        from lindy_orchestrator.session import SessionManager

        sessions = SessionManager(project_dir / ".orchestrator" / "sessions")
        session = sessions.create(goal="Resume goal")
        plan = _make_plan("Resume goal")
        plan.tasks[0].status = TaskStatus.COMPLETED
        plan.tasks[1].status = TaskStatus.FAILED
        plan.tasks[1].result = "error"
        session.plan_json = plan_to_dict(plan)
        session.status = "paused"
        sessions.save(session)

        result = runner.invoke(app, ["resume", "-c", cfg_path])
        assert result.exit_code == 0
        assert "Resume" in result.output
        assert "retry" in result.output or "remaining" in result.output

    def test_resume_no_session(self, project_dir, cfg_path):
        result = runner.invoke(app, ["resume", "-c", cfg_path])
        assert result.exit_code != 0
        assert "No session found" in result.output

    @patch("lindy_orchestrator.scheduler.execute_plan", side_effect=_mock_execute_plan)
    def test_resume_already_completed(self, mock_exec, project_dir, cfg_path):
        from lindy_orchestrator.session import SessionManager

        sessions = SessionManager(project_dir / ".orchestrator" / "sessions")
        session = sessions.create(goal="Done goal")
        session.status = "completed"
        sessions.save(session)

        result = runner.invoke(app, ["resume", "-c", cfg_path])
        assert result.exit_code == 0
        assert "already completed" in result.output.lower()

    @patch("lindy_orchestrator.scheduler.execute_plan", side_effect=_mock_execute_plan)
    def test_resume_by_session_id(self, mock_exec, project_dir, cfg_path):
        from lindy_orchestrator.models import plan_to_dict
        from lindy_orchestrator.session import SessionManager

        sessions = SessionManager(project_dir / ".orchestrator" / "sessions")
        session = sessions.create(goal="Specific session")
        plan = _make_plan("Specific session")
        plan.tasks[0].status = TaskStatus.COMPLETED
        plan.tasks[1].status = TaskStatus.PENDING
        session.plan_json = plan_to_dict(plan)
        session.status = "paused"
        sessions.save(session)

        result = runner.invoke(app, ["resume", session.session_id, "-c", cfg_path])
        assert result.exit_code == 0
        assert session.session_id in result.output


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
        (tmp_path / "orchestrator.yaml").write_text(yaml.dump(config))
        result = runner.invoke(app, ["validate", "-c", str(tmp_path / "orchestrator.yaml")])
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
            patch("lindy_orchestrator.cli_onboard.find_claude_cli", return_value="/usr/bin/claude"),
            patch("lindy_orchestrator.cli_onboard.create_provider") as mock_pf,
        ):
            mock_pf.return_value.dispatch_simple.side_effect = mock_dispatch
            result = runner.invoke(app, ["onboard", "A Python API project", "-y"])
            assert result.exit_code == 0
            assert "scaffold mode" in result.output.lower()
            assert "Onboarding complete" in result.output
            assert (tmp_path / "orchestrator.yaml").exists()

    def test_onboard_init_mode(self, tmp_path, monkeypatch):
        """Existing project without config triggers init+onboard mode."""
        monkeypatch.chdir(tmp_path)
        backend = tmp_path / "backend"
        backend.mkdir()
        (backend / "pyproject.toml").write_text('[project]\nname = "test"')

        result = runner.invoke(app, ["onboard", "-y"])
        # Should detect init+onboard mode
        assert "init+onboard" in result.output.lower() or result.exit_code == 0

    def test_onboard_re_onboard_mode(self, tmp_path, monkeypatch):
        """Project with existing orchestrator.yaml triggers re-onboard mode."""
        monkeypatch.chdir(tmp_path)
        config = {
            "project": {"name": "existing"},
            "modules": [{"name": "app", "path": "app/"}],
        }
        (tmp_path / "orchestrator.yaml").write_text(yaml.dump(config))
        (tmp_path / "app").mkdir()
        (tmp_path / "app" / "pyproject.toml").write_text('[project]\nname = "app"')

        result = runner.invoke(app, ["onboard", "-y"])
        assert "re-onboard" in result.output.lower()

    def test_onboard_scaffold_no_claude_cli(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        with patch("lindy_orchestrator.cli_onboard.find_claude_cli", return_value=None):
            result = runner.invoke(app, ["onboard", "A project", "-y"])
            assert result.exit_code != 0
            assert "Claude CLI not found" in result.output

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
            patch("lindy_orchestrator.cli_onboard.find_claude_cli", return_value="/usr/bin/claude"),
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
        bad = tmp_path / "orchestrator.yaml"
        bad.write_text("invalid: yaml: [broken")
        result = runner.invoke(app, ["status", "-c", str(bad)])
        assert result.exit_code != 0

    def test_empty_project_dir_validate(self, tmp_path):
        """Validate on an empty dir with no config should fail."""
        result = runner.invoke(app, ["validate", "-c", str(tmp_path / "orchestrator.yaml")])
        assert result.exit_code != 0

    def test_no_args_shows_help(self):
        """Invoking without args/commands shows help text."""
        result = runner.invoke(app, [])
        # Typer with no_args_is_help=True returns exit code 0 or 2
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
        (tmp_path / "orchestrator.yaml").write_text(yaml.dump(config))
        result = runner.invoke(
            app, ["status", "-c", str(tmp_path / "orchestrator.yaml"), "--status-only"]
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
        # The "No saved plan found" message appears before the re-run attempt
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
        (tmp_path / "orchestrator.yaml").write_text(yaml.dump(config))
        (tmp_path / "x").mkdir()

        mock_tracker = MagicMock()
        mock_tracker.fetch_issues.return_value = [
            TrackerIssue(id="42", title="Fix bug", body="Details"),
        ]
        mock_create.return_value = mock_tracker

        result = runner.invoke(app, ["issues", "-c", str(tmp_path / "orchestrator.yaml")])
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
        (tmp_path / "orchestrator.yaml").write_text(yaml.dump(config))
        (tmp_path / "x").mkdir()

        mock_tracker = MagicMock()
        mock_tracker.fetch_issues.return_value = [
            TrackerIssue(id="1", title="Issue", body="body"),
        ]
        mock_create.return_value = mock_tracker

        result = runner.invoke(app, ["issues", "-c", str(tmp_path / "orchestrator.yaml"), "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert len(data) == 1
