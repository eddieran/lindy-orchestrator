"""CLI end-to-end coverage for the planner -> generator -> evaluator pipeline."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from lindy_orchestrator.cli import app
from lindy_orchestrator.models import (
    TaskSpec,
    TaskPlan,
    TaskStatus,
    plan_to_dict,
)
from lindy_orchestrator.session import SessionManager

runner = CliRunner()


class _FakeDashboard:
    def __init__(self):
        self.stop = MagicMock()


def _latest_session(project_dir: Path):
    sessions = SessionManager(project_dir / ".orchestrator" / "sessions")
    latest = sessions.load_latest()
    assert latest is not None
    return latest


class TestPipelineRunE2E:
    @patch("lindy_orchestrator.cli.validate_provider")
    @patch("lindy_orchestrator.orchestrator.execute_plan")
    @patch("lindy_orchestrator.planner_runner.generate_plan")
    def test_run_pipeline_persists_mid_execution_checkpoints(
        self,
        mock_generate_plan,
        mock_execute_plan,
        _mock_validate_provider,
        project_dir: Path,
        cfg_path: str,
    ) -> None:
        mock_generate_plan.return_value = TaskPlan(
            goal="Ship the pipeline",
            tasks=[
                TaskSpec(id=1, module="backend", description="Implement API", skip_qa=True),
                TaskSpec(
                    id=2,
                    module="frontend",
                    description="Build UI",
                    depends_on=[1],
                    skip_qa=True,
                ),
            ],
        )

        def execute_with_checkpoints(
            plan,
            cfg,
            logger,
            on_progress=None,
            verbose=False,
            hooks=None,
            session_mgr=None,
            session=None,
        ):
            assert session_mgr is not None
            assert session is not None
            plan.tasks[0].status = TaskStatus.COMPLETED
            session_mgr.checkpoint(session, plan_to_dict(plan))
            plan.tasks[1].status = TaskStatus.COMPLETED
            session_mgr.checkpoint(session, plan_to_dict(plan))
            return plan

        mock_execute_plan.side_effect = execute_with_checkpoints

        result = runner.invoke(app, ["run", "Ship the pipeline", "-c", cfg_path])

        assert result.exit_code == 0
        latest = _latest_session(project_dir)
        assert latest.status == "completed"
        assert latest.checkpoint_count == 2
        assert [task["status"] for task in latest.plan_json["tasks"]] == ["completed", "completed"]

    @patch("lindy_orchestrator.cli.validate_provider")
    @patch("lindy_orchestrator.orchestrator.execute_plan")
    @patch("lindy_orchestrator.planner_runner.generate_plan")
    def test_run_pipeline_with_provider_flag_reaches_planner_and_generator(
        self,
        mock_generate_plan,
        mock_execute_plan,
        mock_validate_provider,
        cfg_path: str,
    ) -> None:
        def generate_with_provider(goal, cfg, on_progress=None, progress=None, hooks=None):
            del hooks
            assert cfg.dispatcher.provider == "codex_cli"
            return TaskPlan(
                goal=goal,
                tasks=[TaskSpec(id=1, module="backend", description="Implement API", skip_qa=True)],
            )

        def execute_with_provider(
            plan,
            cfg,
            logger,
            on_progress=None,
            verbose=False,
            hooks=None,
            session_mgr=None,
            session=None,
        ):
            assert cfg.dispatcher.provider == "codex_cli"
            plan.tasks[0].status = TaskStatus.COMPLETED
            return plan

        mock_generate_plan.side_effect = generate_with_provider
        mock_execute_plan.side_effect = execute_with_provider

        result = runner.invoke(
            app, ["run", "Ship the pipeline", "--provider", "codex_cli", "-c", cfg_path]
        )

        assert result.exit_code == 0
        mock_validate_provider.assert_called_once_with("codex_cli")

    @patch("lindy_orchestrator.cli._start_web_dashboard")
    @patch("lindy_orchestrator.cli.validate_provider")
    @patch("lindy_orchestrator.orchestrator.execute_plan")
    @patch("lindy_orchestrator.planner_runner.generate_plan")
    def test_run_pipeline_with_web_flag_starts_and_stops_dashboard(
        self,
        mock_generate_plan,
        mock_execute_plan,
        _mock_validate_provider,
        mock_start_web_dashboard,
        cfg_path: str,
    ) -> None:
        mock_generate_plan.return_value = TaskPlan(
            goal="Ship the pipeline",
            tasks=[TaskSpec(id=1, module="backend", description="Implement API", skip_qa=True)],
        )
        mock_execute_plan.side_effect = lambda *args, **kwargs: args[0]
        dashboard = _FakeDashboard()
        mock_start_web_dashboard.return_value = dashboard

        result = runner.invoke(app, ["run", "Ship the pipeline", "--web", "-c", cfg_path])

        assert result.exit_code == 0
        dashboard.stop.assert_called_once()

    @patch("lindy_orchestrator.orchestrator.execute_plan")
    def test_resume_pipeline_retries_failed_and_unskips_dependents(
        self,
        mock_execute_plan,
        project_dir: Path,
        cfg_path: str,
    ) -> None:
        sessions = SessionManager(project_dir / ".orchestrator" / "sessions")
        session = sessions.create(goal="Resume pipeline")
        session.status = "paused"
        session.plan_json = plan_to_dict(
            TaskPlan(
                goal="Resume pipeline",
                tasks=[
                    TaskSpec(
                        id=1,
                        module="backend",
                        description="Retry me",
                        status=TaskStatus.FAILED,
                        skip_qa=True,
                    ),
                    TaskSpec(
                        id=2,
                        module="frontend",
                        description="Blocked until retry",
                        depends_on=[1],
                        status=TaskStatus.SKIPPED,
                        skip_qa=True,
                    ),
                ],
            )
        )
        sessions.save(session)

        def execute_resumed_plan(
            plan,
            cfg,
            logger,
            on_progress=None,
            verbose=False,
            hooks=None,
            session_mgr=None,
            session=None,
        ):
            assert plan.tasks[0].status == TaskStatus.PENDING
            assert plan.tasks[1].status == TaskStatus.PENDING
            assert session_mgr is not None
            assert session is not None
            plan.tasks[0].status = TaskStatus.COMPLETED
            session_mgr.checkpoint(session, plan_to_dict(plan))
            plan.tasks[1].status = TaskStatus.COMPLETED
            session_mgr.checkpoint(session, plan_to_dict(plan))
            return plan

        mock_execute_plan.side_effect = execute_resumed_plan

        result = runner.invoke(app, ["resume", "-c", cfg_path])

        assert result.exit_code == 0
        assert "retry" in result.output
        assert "unskipped" in result.output
        latest = _latest_session(project_dir)
        assert latest.status == "completed"
        assert latest.checkpoint_count == 2

    @patch("lindy_orchestrator.cli._start_web_dashboard")
    @patch("lindy_orchestrator.orchestrator.execute_plan")
    def test_resume_pipeline_with_web_flag_starts_and_stops_dashboard(
        self,
        mock_execute_plan,
        mock_start_web_dashboard,
        project_dir: Path,
        cfg_path: str,
    ) -> None:
        sessions = SessionManager(project_dir / ".orchestrator" / "sessions")
        session = sessions.create(goal="Resume with web")
        session.status = "paused"
        session.plan_json = plan_to_dict(
            TaskPlan(
                goal="Resume with web",
                tasks=[TaskSpec(id=1, module="backend", description="Resume me", skip_qa=True)],
            )
        )
        sessions.save(session)
        mock_execute_plan.side_effect = lambda *args, **kwargs: args[0]
        dashboard = _FakeDashboard()
        mock_start_web_dashboard.return_value = dashboard

        result = runner.invoke(app, ["resume", "--web", "-c", cfg_path])

        assert result.exit_code == 0
        dashboard.stop.assert_called_once()

    @patch("lindy_orchestrator.cli.validate_provider")
    @patch("lindy_orchestrator.orchestrator.execute_plan")
    @patch("lindy_orchestrator.planner_runner.generate_plan")
    def test_run_pipeline_with_eval_retry_completes(
        self,
        mock_generate_plan,
        mock_execute_plan,
        _mock_validate_provider,
        project_dir: Path,
        cfg_path: str,
    ) -> None:
        mock_generate_plan.return_value = TaskPlan(
            goal="Ship the pipeline",
            tasks=[TaskSpec(id=1, module="backend", description="Implement API")],
        )

        def execute_with_retry(
            plan,
            cfg,
            logger,
            on_progress=None,
            verbose=False,
            hooks=None,
            session_mgr=None,
            session=None,
        ):
            assert session_mgr is not None
            assert session is not None
            plan.tasks[0].retries = 1
            plan.tasks[0].status = TaskStatus.COMPLETED
            session_mgr.checkpoint(session, plan_to_dict(plan))
            return plan

        mock_execute_plan.side_effect = execute_with_retry

        result = runner.invoke(app, ["run", "Ship the pipeline", "-c", cfg_path])

        assert result.exit_code == 0
        latest = _latest_session(project_dir)
        assert latest.status == "completed"
        assert latest.plan_json["tasks"][0]["retries"] == 1
