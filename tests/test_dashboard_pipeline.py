"""Pipeline visualization tests for dashboard, web server, and reporter."""

from __future__ import annotations

import json
import socket
import urllib.request
from io import StringIO
from pathlib import Path

from rich.console import Console

from lindy_orchestrator.dashboard import Dashboard
from lindy_orchestrator.hooks import Event, EventType, HookRegistry
from lindy_orchestrator.models import (
    AttemptRecord,
    EvalFeedback,
    EvalResult,
    ExecutionResult,
    GeneratorOutput,
    TaskSpec,
    TaskState,
    TaskStatus,
)
from lindy_orchestrator.reporter import generate_execution_summary, save_summary_report
from lindy_orchestrator.web.server import WebDashboard, _INDEX_HTML


def _make_console(width: int = 140) -> Console:
    return Console(file=StringIO(), force_terminal=False, width=width)


def _get_output(console: Console) -> str:
    console.file.seek(0)
    return console.file.read()


def _sample_result() -> ExecutionResult:
    backend = TaskSpec(
        id=1,
        module="backend",
        description="Add scoring pipeline",
        depends_on=[],
        acceptance_criteria="All evaluator scores are visible",
    )
    frontend = TaskSpec(
        id=2,
        module="web",
        description="Render attempt history",
        depends_on=[1],
        acceptance_criteria="Attempt history is visible in the sidebar",
    )
    return ExecutionResult(
        plan=None,
        states=[
            TaskState(
                spec=backend,
                status=TaskStatus.COMPLETED,
                phase="done",
                total_cost_usd=2.25,
                attempts=[
                    AttemptRecord(
                        attempt=1,
                        generator_output=GeneratorOutput(
                            success=True,
                            output="implemented phase tracking",
                            diff="diff --git a b",
                            cost_usd=1.50,
                            duration_seconds=32.0,
                        ),
                        eval_result=EvalResult(
                            score=92,
                            passed=True,
                            feedback=EvalFeedback(summary="Looks good"),
                            cost_usd=0.75,
                            duration_seconds=8.0,
                        ),
                        timestamp="2026-03-28T10:00:00+00:00",
                    )
                ],
            ),
            TaskState(
                spec=frontend,
                status=TaskStatus.IN_PROGRESS,
                phase="evaluating",
                total_cost_usd=2.50,
                attempts=[
                    AttemptRecord(
                        attempt=1,
                        generator_output=GeneratorOutput(
                            success=True,
                            output="first pass",
                            diff="diff --git c d",
                            cost_usd=1.00,
                            duration_seconds=25.0,
                        ),
                        eval_result=EvalResult(
                            score=48,
                            passed=False,
                            feedback=EvalFeedback(summary="History table missing"),
                            cost_usd=0.50,
                            duration_seconds=7.0,
                        ),
                        timestamp="2026-03-28T10:05:00+00:00",
                    ),
                    AttemptRecord(
                        attempt=2,
                        generator_output=GeneratorOutput(
                            success=True,
                            output="second pass",
                            diff="diff --git e f",
                            cost_usd=0.70,
                            duration_seconds=18.0,
                        ),
                        eval_result=EvalResult(
                            score=78,
                            passed=False,
                            feedback=EvalFeedback(summary="Controls still disabled"),
                            cost_usd=0.30,
                            duration_seconds=5.0,
                        ),
                        timestamp="2026-03-28T10:08:00+00:00",
                    ),
                ],
            ),
        ],
        duration_seconds=120.0,
        total_cost_usd=4.75,
        session_id="sess-pipeline",
        goal="Ship the pipeline dashboard",
    )


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _read_sse_init(url: str) -> dict:
    with urllib.request.urlopen(f"{url}/events", timeout=2) as response:
        event_name = response.readline().decode().strip()
        payload = response.readline().decode().strip()
        response.readline()
    assert event_name == "event: init"
    assert payload.startswith("data: ")
    return json.loads(payload[6:])


class _RecorderQueue:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int | None]] = []

    def pause(self) -> None:
        self.calls.append(("pause", None))

    def resume(self) -> None:
        self.calls.append(("resume", None))

    def skip(self, task_id: int) -> None:
        self.calls.append(("skip", task_id))

    def force_pass(self, task_id: int) -> None:
        self.calls.append(("force_pass", task_id))


class TestPipelineDashboard:
    def test_phase_changed_updates_annotation(self):
        result = _sample_result()
        hooks = HookRegistry()
        dash = Dashboard(result, hooks, console=_make_console(), verbose=True)
        dash.start()

        hooks.emit(
            Event(
                type=EventType.PHASE_CHANGED,
                task_id=2,
                module="web",
                data={"phase": "generating", "attempt": 2},
            )
        )

        assert dash._annotations[2] == "[Generate -> att. 2]"

    def test_eval_scored_updates_annotation(self):
        result = _sample_result()
        hooks = HookRegistry()
        dash = Dashboard(result, hooks, console=_make_console(), verbose=True)
        dash.start()

        hooks.emit(
            Event(
                type=EventType.EVAL_SCORED,
                task_id=2,
                module="web",
                data={"score": 78, "passed": False, "attempt": 2},
            )
        )

        assert dash._annotations[2] == "[Evaluate -> 78/100]"

    def test_completed_tasks_show_last_score_in_annotation(self):
        result = _sample_result()
        hooks = HookRegistry()
        dash = Dashboard(result, hooks, console=_make_console(), verbose=True)
        dash.start()

        hooks.emit(
            Event(
                type=EventType.EVAL_SCORED,
                task_id=1,
                module="backend",
                data={"score": 92, "passed": True, "attempt": 1},
            )
        )
        hooks.emit(Event(type=EventType.TASK_COMPLETED, task_id=1, module="backend"))

        assert dash._annotations[1] == "(92/100)"

    def test_summary_includes_total_cost(self):
        result = _sample_result()
        hooks = HookRegistry()
        dash = Dashboard(result, hooks, console=_make_console(), verbose=True)

        summary = dash._build_summary()

        assert "$4.75" in summary.plain


class TestPipelineWebDashboard:
    def test_html_contains_controls(self):
        assert '<div class="controls">' in _INDEX_HTML
        assert "Pause" in _INDEX_HTML
        assert "Resume" in _INDEX_HTML
        assert "Skip" in _INDEX_HTML
        assert "Force Pass" in _INDEX_HTML

    def test_init_payload_includes_pipeline_fields(self):
        result = _sample_result()
        hooks = HookRegistry()
        dash = WebDashboard(result, hooks, port=_free_port())
        dash.start()

        try:
            payload = _read_sse_init(dash.url)
        finally:
            dash.stop()

        task = payload["tasks"][0]
        assert payload["goal"] == "Ship the pipeline dashboard"
        assert task["acceptance_criteria"] == "All evaluator scores are visible"
        assert task["phase"] == "done"
        assert task["attempts"][0]["attempt"] == 1

    def test_post_pause_routes_to_command_queue(self):
        result = _sample_result()
        hooks = HookRegistry()
        queue = _RecorderQueue()
        dash = WebDashboard(result, hooks, command_queue=queue, port=_free_port())
        dash.start()

        try:
            with urllib.request.urlopen(
                urllib.request.Request(f"{dash.url}/api/pause", method="POST"), timeout=2
            ) as response:
                assert response.status == 200
        finally:
            dash.stop()

        assert queue.calls == [("pause", None)]

    def test_post_resume_routes_to_command_queue(self):
        result = _sample_result()
        hooks = HookRegistry()
        queue = _RecorderQueue()
        dash = WebDashboard(result, hooks, command_queue=queue, port=_free_port())
        dash.start()

        try:
            with urllib.request.urlopen(
                urllib.request.Request(f"{dash.url}/api/resume", method="POST"), timeout=2
            ) as response:
                assert response.status == 200
        finally:
            dash.stop()

        assert queue.calls == [("resume", None)]

    def test_post_task_skip_routes_to_command_queue(self):
        result = _sample_result()
        hooks = HookRegistry()
        queue = _RecorderQueue()
        dash = WebDashboard(result, hooks, command_queue=queue, port=_free_port())
        dash.start()

        try:
            with urllib.request.urlopen(
                urllib.request.Request(f"{dash.url}/api/task/2/skip", method="POST"),
                timeout=2,
            ) as response:
                assert response.status == 200
        finally:
            dash.stop()

        assert queue.calls == [("skip", 2)]

    def test_post_task_force_pass_routes_to_command_queue(self):
        result = _sample_result()
        hooks = HookRegistry()
        queue = _RecorderQueue()
        dash = WebDashboard(result, hooks, command_queue=queue, port=_free_port())
        dash.start()

        try:
            with urllib.request.urlopen(
                urllib.request.Request(f"{dash.url}/api/task/2/force-pass", method="POST"),
                timeout=2,
            ) as response:
                assert response.status == 200
        finally:
            dash.stop()

        assert queue.calls == [("force_pass", 2)]


class TestPipelineReporter:
    def test_console_summary_includes_attempt_history_and_cost_breakdown(self):
        result = _sample_result()
        console = _make_console(width=220)

        generate_execution_summary(result, console=console)

        output = _get_output(console)
        assert "Attempt History" in output
        assert "Planner" in output
        assert "Generator" in output
        assert "Evaluator" in output

    def test_markdown_report_includes_attempt_history_and_cost_breakdown(self, tmp_path: Path):
        result = _sample_result()

        report_path = save_summary_report(result, root=tmp_path)

        content = report_path.read_text()
        assert "## Cost Breakdown" in content
        assert "## Attempt History" in content
        assert "History table missing" in content
        assert "$4.75" in content
