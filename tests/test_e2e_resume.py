"""End-to-end CLI tests — resume command and execution summary report for resume.

Split from test_e2e_core.py to keep files under 500 lines.
Uses Typer CliRunner, mocking only external dependencies (Claude CLI, git, LLM).
"""

from __future__ import annotations

from unittest.mock import patch

from typer.testing import CliRunner

from lindy_orchestrator.cli import app
from lindy_orchestrator.models import TaskStatus

from .conftest import make_plan, mock_execute_plan

runner = CliRunner()


# ---------------------------------------------------------------------------
# 6. Resume command
# ---------------------------------------------------------------------------


class TestE2EResume:
    @patch("lindy_orchestrator.orchestrator.execute_plan", side_effect=mock_execute_plan)
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

    @patch("lindy_orchestrator.orchestrator.execute_plan", side_effect=mock_execute_plan)
    def test_resume_already_completed(self, mock_exec, project_dir, cfg_path):
        from lindy_orchestrator.session import SessionManager

        sessions = SessionManager(project_dir / ".orchestrator" / "sessions")
        session = sessions.create(goal="Done goal")
        session.status = "completed"
        sessions.save(session)

        result = runner.invoke(app, ["resume", "-c", cfg_path])
        assert result.exit_code == 0
        assert "already completed" in result.output.lower()

    @patch("lindy_orchestrator.orchestrator.execute_plan", side_effect=mock_execute_plan)
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

    # --- Execution summary report E2E tests for resume ---

    @patch("lindy_orchestrator.orchestrator.execute_plan", side_effect=mock_execute_plan)
    def test_resume_shows_execution_summary(self, mock_exec, project_dir, cfg_path):
        """Resume command outputs the execution summary after re-execution."""
        from lindy_orchestrator.models import plan_to_dict
        from lindy_orchestrator.session import SessionManager

        sessions = SessionManager(project_dir / ".orchestrator" / "sessions")
        session = sessions.create(goal="Summary resume")
        plan = make_plan("Summary resume")
        plan.tasks[0].status = TaskStatus.COMPLETED
        plan.tasks[1].status = TaskStatus.FAILED
        plan.tasks[1].result = "err"
        session.plan_json = plan_to_dict(plan)
        session.status = "paused"
        sessions.save(session)

        result = runner.invoke(app, ["resume", "-c", cfg_path])
        assert result.exit_code == 0
        assert "Task Details" in result.output
        assert "Execution Metrics" in result.output

    @patch("lindy_orchestrator.orchestrator.execute_plan", side_effect=mock_execute_plan)
    def test_resume_shows_goal_completed(self, mock_exec, project_dir, cfg_path):
        """Resume that completes all tasks shows GOAL COMPLETED."""
        from lindy_orchestrator.models import plan_to_dict
        from lindy_orchestrator.session import SessionManager

        sessions = SessionManager(project_dir / ".orchestrator" / "sessions")
        session = sessions.create(goal="Complete on resume")
        plan = make_plan("Complete on resume")
        plan.tasks[0].status = TaskStatus.COMPLETED
        plan.tasks[1].status = TaskStatus.FAILED
        plan.tasks[1].result = "err"
        session.plan_json = plan_to_dict(plan)
        session.status = "paused"
        sessions.save(session)

        result = runner.invoke(app, ["resume", "-c", cfg_path])
        assert result.exit_code == 0
        assert "GOAL COMPLETED" in result.output

    @patch("lindy_orchestrator.orchestrator.execute_plan", side_effect=mock_execute_plan)
    def test_resume_saves_report_file(self, mock_exec, project_dir, cfg_path):
        """Resume command saves a Markdown report to .orchestrator/reports/."""
        from lindy_orchestrator.models import plan_to_dict
        from lindy_orchestrator.session import SessionManager

        sessions = SessionManager(project_dir / ".orchestrator" / "sessions")
        session = sessions.create(goal="Resume report save")
        plan = make_plan("Resume report save")
        plan.tasks[0].status = TaskStatus.COMPLETED
        plan.tasks[1].status = TaskStatus.PENDING
        session.plan_json = plan_to_dict(plan)
        session.status = "paused"
        sessions.save(session)

        result = runner.invoke(app, ["resume", "-c", cfg_path])
        assert result.exit_code == 0
        assert "Report saved to" in result.output
        reports_dir = project_dir / ".orchestrator" / "reports"
        assert reports_dir.exists()
        report_files = list(reports_dir.glob("*_summary.md"))
        assert len(report_files) == 1

    @patch("lindy_orchestrator.orchestrator.execute_plan")
    def test_resume_with_failures_shows_paused(self, mock_exec, project_dir, cfg_path):
        """Resume that still has failures shows GOAL PAUSED."""
        from lindy_orchestrator.models import plan_to_dict
        from lindy_orchestrator.session import SessionManager

        def exec_still_fails(
            plan,
            cfg,
            logger,
            on_progress=None,
            verbose=False,
            hooks=None,
            session_mgr=None,
            session=None,
        ):
            for t in plan.tasks:
                if t.status == TaskStatus.PENDING:
                    t.status = TaskStatus.FAILED
                    t.result = "still broken"
            return plan

        mock_exec.side_effect = exec_still_fails

        sessions = SessionManager(project_dir / ".orchestrator" / "sessions")
        session = sessions.create(goal="Fails on resume")
        plan = make_plan("Fails on resume")
        plan.tasks[0].status = TaskStatus.COMPLETED
        plan.tasks[1].status = TaskStatus.FAILED
        plan.tasks[1].result = "broken"
        session.plan_json = plan_to_dict(plan)
        session.status = "paused"
        sessions.save(session)

        result = runner.invoke(app, ["resume", "-c", cfg_path])
        assert result.exit_code == 0
        assert "GOAL PAUSED" in result.output
        assert "Task Details" in result.output
