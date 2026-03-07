"""End-to-end CLI tests — core commands (version, status, mailbox, run, plan, resume).

Uses Typer CliRunner, mocking only external dependencies (Claude CLI, git, LLM).
"""

from __future__ import annotations

import json
from unittest.mock import patch

from typer.testing import CliRunner

from lindy_orchestrator import __version__
from lindy_orchestrator.cli import app
from lindy_orchestrator.mailbox import Mailbox, Message
from lindy_orchestrator.models import TaskStatus

from .conftest import make_plan, mock_execute_plan, mock_generate_plan

runner = CliRunner()


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
        import yaml

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

        result = runner.invoke(app, ["mailbox", "backend", "-c", cfg_path])
        assert result.exit_code == 0
        assert "API ready?" in result.output
        assert "frontend" in result.output

    def test_mailbox_send_default_from(self, project_dir, cfg_path):
        result = runner.invoke(
            app,
            ["mailbox", "--send-to", "backend", "-m", "hello", "-c", cfg_path],
        )
        assert result.exit_code == 0
        mb = Mailbox(project_dir / ".orchestrator" / "mailbox")
        msgs = mb.receive("backend")
        assert msgs[0].from_module == "cli"

    def test_mailbox_send_requires_message(self, cfg_path):
        result = runner.invoke(app, ["mailbox", "--send-to", "backend", "-c", cfg_path])
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
        import yaml

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
    @patch("lindy_orchestrator.scheduler.execute_plan", side_effect=mock_execute_plan)
    @patch("lindy_orchestrator.planner.generate_plan", side_effect=mock_generate_plan)
    def test_run_dry_run(self, mock_plan, mock_exec, mock_cli, cfg_path):
        result = runner.invoke(app, ["run", "Build a feature", "--dry-run", "-c", cfg_path])
        assert result.exit_code == 0
        assert "tasks planned" in result.output
        call_args = mock_exec.call_args
        assert call_args[0][1].safety.dry_run is True

    @patch("lindy_orchestrator.cli.find_claude_cli", return_value="/usr/bin/claude")
    @patch("lindy_orchestrator.scheduler.execute_plan", side_effect=mock_execute_plan)
    @patch("lindy_orchestrator.planner.generate_plan", side_effect=mock_generate_plan)
    def test_run_full_flow(self, mock_plan, mock_exec, mock_cli, cfg_path):
        result = runner.invoke(app, ["run", "Implement auth", "-c", cfg_path])
        assert result.exit_code == 0
        assert "GOAL COMPLETED" in result.output

    @patch("lindy_orchestrator.cli.find_claude_cli", return_value=None)
    def test_run_no_claude_cli(self, mock_cli, cfg_path):
        result = runner.invoke(app, ["run", "test goal", "-c", cfg_path])
        assert result.exit_code != 0
        assert "Claude CLI not found" in result.output

    def test_run_no_goal(self, cfg_path):
        result = runner.invoke(app, ["run", "-c", cfg_path])
        assert result.exit_code != 0

    @patch("lindy_orchestrator.cli.find_claude_cli", return_value="/usr/bin/claude")
    @patch("lindy_orchestrator.scheduler.execute_plan", side_effect=mock_execute_plan)
    def test_run_from_plan_file(self, mock_exec, mock_cli, project_dir, cfg_path):
        """Run from a saved plan JSON (skip planning step)."""
        from lindy_orchestrator.models import plan_to_dict

        plan = make_plan("Plan file goal")
        for t in plan.tasks:
            t.status = TaskStatus.PENDING
        plan_file = project_dir / "plan.json"
        plan_file.write_text(json.dumps(plan_to_dict(plan), indent=2, default=str))

        result = runner.invoke(app, ["run", "--plan", str(plan_file), "-c", cfg_path])
        assert result.exit_code == 0
        assert "Loaded plan from" in result.output

    @patch("lindy_orchestrator.cli.find_claude_cli", return_value="/usr/bin/claude")
    def test_run_plan_file_not_found(self, mock_cli, cfg_path):
        result = runner.invoke(app, ["run", "--plan", "/nonexistent/plan.json", "-c", cfg_path])
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
    @patch("lindy_orchestrator.planner.generate_plan", side_effect=mock_generate_plan)
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
    @patch("lindy_orchestrator.planner.generate_plan", side_effect=mock_generate_plan)
    def test_plan_basic(self, mock_plan, cfg_path):
        result = runner.invoke(app, ["plan", "Build an API", "-c", cfg_path])
        assert result.exit_code == 0
        assert "2 tasks" in result.output
        assert "Plan saved to" in result.output

    @patch("lindy_orchestrator.planner.generate_plan", side_effect=mock_generate_plan)
    def test_plan_with_output_file(self, mock_plan, project_dir, cfg_path):
        out_file = project_dir / "myplan.json"
        result = runner.invoke(
            app,
            ["plan", "Build an API", "-c", cfg_path, "-o", str(out_file)],
        )
        assert result.exit_code == 0
        assert out_file.exists()
        data = json.loads(out_file.read_text())
        assert data["goal"] == "Build an API"
        assert len(data["tasks"]) == 2

    def test_plan_no_goal(self, cfg_path):
        result = runner.invoke(app, ["plan", "-c", cfg_path])
        assert result.exit_code != 0

    @patch("lindy_orchestrator.planner.generate_plan", side_effect=mock_generate_plan)
    def test_plan_from_file(self, mock_plan, project_dir, cfg_path):
        goal_file = project_dir / "goal.md"
        goal_file.write_text("Implement feature X with tests")
        result = runner.invoke(app, ["plan", "--file", str(goal_file), "-c", cfg_path])
        assert result.exit_code == 0
        assert "2 tasks" in result.output


# ---------------------------------------------------------------------------
# 6. Resume command
# ---------------------------------------------------------------------------


class TestE2EResume:
    @patch("lindy_orchestrator.scheduler.execute_plan", side_effect=mock_execute_plan)
    def test_resume_latest_session(self, mock_exec, project_dir, cfg_path):
        """Resume picks up the latest session and re-executes pending tasks."""
        from lindy_orchestrator.models import plan_to_dict
        from lindy_orchestrator.session import SessionManager

        sessions = SessionManager(project_dir / ".orchestrator" / "sessions")
        session = sessions.create(goal="Resume goal")
        plan = make_plan("Resume goal")
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

    @patch("lindy_orchestrator.scheduler.execute_plan", side_effect=mock_execute_plan)
    def test_resume_already_completed(self, mock_exec, project_dir, cfg_path):
        from lindy_orchestrator.session import SessionManager

        sessions = SessionManager(project_dir / ".orchestrator" / "sessions")
        session = sessions.create(goal="Done goal")
        session.status = "completed"
        sessions.save(session)

        result = runner.invoke(app, ["resume", "-c", cfg_path])
        assert result.exit_code == 0
        assert "already completed" in result.output.lower()

    @patch("lindy_orchestrator.scheduler.execute_plan", side_effect=mock_execute_plan)
    def test_resume_by_session_id(self, mock_exec, project_dir, cfg_path):
        from lindy_orchestrator.models import plan_to_dict
        from lindy_orchestrator.session import SessionManager

        sessions = SessionManager(project_dir / ".orchestrator" / "sessions")
        session = sessions.create(goal="Specific session")
        plan = make_plan("Specific session")
        plan.tasks[0].status = TaskStatus.COMPLETED
        plan.tasks[1].status = TaskStatus.PENDING
        session.plan_json = plan_to_dict(plan)
        session.status = "paused"
        sessions.save(session)

        result = runner.invoke(app, ["resume", session.session_id, "-c", cfg_path])
        assert result.exit_code == 0
        assert session.session_id in result.output
