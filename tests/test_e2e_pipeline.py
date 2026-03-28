"""CLI end-to-end coverage for the planner -> generator -> evaluator pipeline."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from lindy_orchestrator.cli import app
from lindy_orchestrator.models import (
    DispatchResult,
    QAResult,
    TaskSpec,
    TaskPlan,
    TaskStatus,
    plan_to_dict,
)
from lindy_orchestrator.session import SessionManager

runner = CliRunner()


def _task_payload(
    task_id: int,
    module: str,
    description: str,
    *,
    depends_on: list[int] | None = None,
    skip_qa: bool = False,
) -> dict:
    payload: dict[str, object] = {
        "id": task_id,
        "module": module,
        "description": description,
        "skip_qa": skip_qa,
    }
    if depends_on is not None:
        payload["depends_on"] = depends_on
    return payload


def _plan_output(*tasks: dict) -> str:
    return json.dumps({"tasks": list(tasks)})


class _ValidationProvider:
    def validate(self) -> None:
        return None


class _RecordingProvider:
    def __init__(self, side_effect=None):
        self.side_effect = side_effect
        self.dispatch_calls: list[dict[str, object]] = []
        self.validate_calls = 0

    def validate(self) -> None:
        self.validate_calls += 1

    def dispatch(
        self,
        module: str,
        working_dir: Path,
        prompt: str,
        on_event=None,
        stall_seconds: int | None = None,
    ) -> DispatchResult:
        call = {
            "module": module,
            "working_dir": Path(working_dir),
            "prompt": prompt,
            "stall_seconds": stall_seconds,
            "on_event": on_event,
        }
        self.dispatch_calls.append(call)
        if self.side_effect is None:
            return DispatchResult(module=module, success=True, output="done", event_count=1)
        return self.side_effect(call)


class _FakeDashboard:
    def __init__(self):
        self.stop = MagicMock()


def _latest_session(project_dir: Path):
    sessions = SessionManager(project_dir / ".orchestrator" / "sessions")
    latest = sessions.load_latest()
    assert latest is not None
    return latest


class TestPipelineRunE2E:
    @patch("lindy_orchestrator.orchestrator.create_worktree", return_value=None)
    @patch("lindy_orchestrator.orchestrator.create_provider")
    @patch("lindy_orchestrator.planner_runner.create_provider")
    @patch("lindy_orchestrator.cli_helpers.create_provider")
    def test_run_pipeline_persists_mid_execution_checkpoints(
        self,
        mock_validate_create_provider,
        mock_planner_create_provider,
        mock_generator_create_provider,
        _mock_create_worktree,
        project_dir: Path,
        cfg_path: str,
    ) -> None:
        planner = _RecordingProvider(
            side_effect=lambda _call: DispatchResult(
                module="planner",
                success=True,
                output=_plan_output(
                    _task_payload(1, "backend", "Implement API", skip_qa=True),
                    _task_payload(2, "frontend", "Build UI", depends_on=[1], skip_qa=True),
                ),
                event_count=2,
            )
        )
        generator = _RecordingProvider()
        mock_validate_create_provider.return_value = _ValidationProvider()
        mock_planner_create_provider.return_value = planner
        mock_generator_create_provider.side_effect = lambda _cfg: generator

        result = runner.invoke(app, ["run", "Ship the pipeline", "-c", cfg_path])

        assert result.exit_code == 0
        latest = _latest_session(project_dir)
        assert latest.status == "completed"
        assert latest.checkpoint_count == 2
        assert [task["status"] for task in latest.plan_json["tasks"]] == ["completed", "completed"]

    @patch("lindy_orchestrator.orchestrator.create_worktree", return_value=None)
    @patch("lindy_orchestrator.orchestrator.create_provider")
    @patch("lindy_orchestrator.planner_runner.create_provider")
    @patch("lindy_orchestrator.cli_helpers.create_provider")
    def test_run_pipeline_with_provider_flag_reaches_planner_and_generator(
        self,
        mock_validate_create_provider,
        mock_planner_create_provider,
        mock_generator_create_provider,
        _mock_create_worktree,
        cfg_path: str,
    ) -> None:
        def planner_side_effect(call: dict[str, object]) -> DispatchResult:
            return DispatchResult(
                module="planner",
                success=True,
                output=_plan_output(_task_payload(1, "backend", "Implement API", skip_qa=True)),
                event_count=1,
            )

        planner = _RecordingProvider(side_effect=planner_side_effect)
        generator = _RecordingProvider()
        mock_validate_create_provider.side_effect = lambda cfg: _ValidationProvider()
        mock_planner_create_provider.side_effect = lambda cfg: (
            planner if cfg.provider == "codex_cli" else None
        )
        mock_generator_create_provider.side_effect = lambda cfg: (
            generator if cfg.provider == "codex_cli" else None
        )

        result = runner.invoke(
            app, ["run", "Ship the pipeline", "--provider", "codex_cli", "-c", cfg_path]
        )

        assert result.exit_code == 0
        assert planner.dispatch_calls[0]["module"] == "planner"
        assert generator.dispatch_calls[0]["module"] == "backend"

    @patch("lindy_orchestrator.cli._start_web_dashboard")
    @patch("lindy_orchestrator.orchestrator.create_worktree", return_value=None)
    @patch("lindy_orchestrator.orchestrator.create_provider")
    @patch("lindy_orchestrator.planner_runner.create_provider")
    @patch("lindy_orchestrator.cli_helpers.create_provider")
    def test_run_pipeline_with_web_flag_starts_and_stops_dashboard(
        self,
        mock_validate_create_provider,
        mock_planner_create_provider,
        mock_generator_create_provider,
        _mock_create_worktree,
        mock_start_web_dashboard,
        cfg_path: str,
    ) -> None:
        mock_validate_create_provider.return_value = _ValidationProvider()
        mock_planner_create_provider.return_value = _RecordingProvider(
            side_effect=lambda _call: DispatchResult(
                module="planner",
                success=True,
                output=_plan_output(_task_payload(1, "backend", "Implement API", skip_qa=True)),
            )
        )
        mock_generator_create_provider.side_effect = lambda _cfg: _RecordingProvider()
        dashboard = _FakeDashboard()
        mock_start_web_dashboard.return_value = dashboard

        result = runner.invoke(app, ["run", "Ship the pipeline", "--web", "-c", cfg_path])

        assert result.exit_code == 0
        dashboard.stop.assert_called_once()

    @patch("lindy_orchestrator.orchestrator.create_worktree", return_value=None)
    @patch("lindy_orchestrator.orchestrator.create_provider")
    def test_resume_pipeline_retries_failed_and_unskips_dependents(
        self,
        mock_generator_create_provider,
        _mock_create_worktree,
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
        mock_generator_create_provider.side_effect = lambda _cfg: _RecordingProvider()

        result = runner.invoke(app, ["resume", "-c", cfg_path])

        assert result.exit_code == 0
        assert "retry" in result.output
        assert "unskipped" in result.output
        latest = _latest_session(project_dir)
        assert latest.status == "completed"
        assert latest.checkpoint_count == 2

    @patch("lindy_orchestrator.cli._start_web_dashboard")
    @patch("lindy_orchestrator.orchestrator.create_worktree", return_value=None)
    @patch("lindy_orchestrator.orchestrator.create_provider")
    def test_resume_pipeline_with_web_flag_starts_and_stops_dashboard(
        self,
        mock_generator_create_provider,
        _mock_create_worktree,
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
        mock_generator_create_provider.side_effect = lambda _cfg: _RecordingProvider()
        dashboard = _FakeDashboard()
        mock_start_web_dashboard.return_value = dashboard

        result = runner.invoke(app, ["resume", "--web", "-c", cfg_path])

        assert result.exit_code == 0
        dashboard.stop.assert_called_once()

    @patch("lindy_orchestrator.orchestrator.prepare_qa_checks")
    @patch("lindy_orchestrator.orchestrator.run_qa_gate")
    @patch("lindy_orchestrator.orchestrator.create_worktree", return_value=None)
    @patch("lindy_orchestrator.orchestrator.create_provider")
    @patch("lindy_orchestrator.planner_runner.create_provider")
    @patch("lindy_orchestrator.cli_helpers.create_provider")
    def test_run_pipeline_with_eval_retry_completes(
        self,
        mock_validate_create_provider,
        mock_planner_create_provider,
        mock_generator_create_provider,
        _mock_create_worktree,
        mock_run_qa_gate,
        _mock_prepare_qa_checks,
        project_dir: Path,
        cfg_path: str,
    ) -> None:
        planner = _RecordingProvider(
            side_effect=lambda _call: DispatchResult(
                module="planner",
                success=True,
                output=json.dumps(
                    {
                        "tasks": [
                            {
                                "id": 1,
                                "module": "backend",
                                "description": "Implement API",
                                "qa_checks": [
                                    {"gate": "command_check", "params": {"command": "pytest"}}
                                ],
                            }
                        ]
                    }
                ),
                event_count=1,
            )
        )
        generator = _RecordingProvider()
        mock_validate_create_provider.return_value = _ValidationProvider()
        mock_planner_create_provider.return_value = planner
        mock_generator_create_provider.side_effect = lambda _cfg: generator
        mock_run_qa_gate.side_effect = [
            QAResult(
                gate="command_check", passed=False, output="FAILED tests/test_api.py::test_create"
            ),
            QAResult(gate="command_check", passed=True, output="ok"),
        ]

        result = runner.invoke(app, ["run", "Ship the pipeline", "-c", cfg_path])

        assert result.exit_code == 0
        latest = _latest_session(project_dir)
        assert latest.status == "completed"
        assert latest.plan_json["tasks"][0]["retries"] == 1
