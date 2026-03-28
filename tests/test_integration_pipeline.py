"""Integration coverage for the planner -> generator -> evaluator pipeline."""

from __future__ import annotations

import json
import os
import socket
import threading
import time
from contextlib import closing
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.request import urlopen

import pytest

from lindy_orchestrator.config import ModuleConfig, OrchestratorConfig
from lindy_orchestrator.dispatch_core import streaming_dispatch
from lindy_orchestrator.hooks import Event, EventType, HookRegistry
from lindy_orchestrator.logger import ActionLogger
from lindy_orchestrator.models import (
    DispatchResult,
    QACheck,
    QAResult,
    TaskSpec,
    TaskPlan,
    TaskStatus,
)
from lindy_orchestrator.planner_runner import generate_plan
from lindy_orchestrator.qa.agent_check import AgentCheckGate
from lindy_orchestrator.session import SessionManager
from lindy_orchestrator.web.server import WebDashboard

from .conftest import MINIMAL_STATUS_MD

# SSE tests require a real HTTP server and are flaky in constrained CI runners
_SKIP_SSE = pytest.mark.skipif(
    os.environ.get("CI") == "true",
    reason="SSE tests are timing-sensitive and flaky in CI",
)


def _make_config(tmp_path: Path, *, include_qa: bool = False) -> OrchestratorConfig:
    modules = [
        ModuleConfig(name="backend", path="backend"),
        ModuleConfig(name="frontend", path="frontend"),
    ]
    if include_qa:
        modules.append(ModuleConfig(name="reviewer", path="qa", role="qa"))

    orch_dir = tmp_path / ".orchestrator"
    status_dir = orch_dir / "status"
    status_dir.mkdir(parents=True, exist_ok=True)

    for module in modules:
        (tmp_path / module.path).mkdir(parents=True, exist_ok=True)
        (status_dir / f"{module.name}.md").write_text(MINIMAL_STATUS_MD.format(name=module.name))

    cfg = OrchestratorConfig(modules=modules)
    cfg._config_dir = tmp_path
    cfg.safety.max_parallel = 4
    return cfg


def _make_logger(tmp_path: Path) -> ActionLogger:
    return ActionLogger(tmp_path / ".orchestrator" / "logs" / "actions.jsonl")


def _plan_json(*tasks: dict) -> str:
    return json.dumps({"tasks": list(tasks)})


def _task_payload(
    task_id: int,
    module: str,
    description: str,
    *,
    depends_on: list[int] | None = None,
    qa_checks: list[dict] | None = None,
) -> dict:
    payload: dict[str, object] = {
        "id": task_id,
        "module": module,
        "description": description,
    }
    if depends_on is not None:
        payload["depends_on"] = depends_on
    if qa_checks is not None:
        payload["qa_checks"] = qa_checks
    return payload


class RecordingProvider:
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
            return DispatchResult(module=module, success=True, output=f"{module} ok", event_count=1)
        return self.side_effect(call)


class _FakeProcess:
    def __init__(self, lines: list[str], *, returncode: int = 0, stderr: str = ""):
        self.stdout = iter(lines)
        self.stderr = StringIO(stderr)
        self.returncode: int | None = None
        self._final_returncode = returncode

    def poll(self) -> int | None:
        return self.returncode

    def wait(self) -> int:
        if self.returncode is None:
            self.returncode = self._final_returncode
        return self.returncode

    def kill(self) -> None:
        self.returncode = -9


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _open_sse(port: int):
    deadline = time.time() + 5
    while True:
        try:
            return urlopen(f"http://127.0.0.1:{port}/events", timeout=5)
        except OSError:
            if time.time() >= deadline:
                raise
            time.sleep(0.1)


def _read_sse_event(response) -> tuple[str, dict]:
    event_name = ""
    data_line = ""
    while True:
        raw = response.readline().decode("utf-8")
        if raw in ("", "\n", "\r\n"):
            break
        if raw.startswith("event: "):
            event_name = raw.removeprefix("event: ").strip()
        elif raw.startswith("data: "):
            data_line = raw.removeprefix("data: ").strip()
    return event_name, json.loads(data_line)


class TestPlannerGeneratorEvaluatorPipeline:
    @patch("lindy_orchestrator.planner_runner.create_provider")
    def test_generate_plan_reads_all_status_context_and_dispatches_planner_role(
        self, mock_create_provider, tmp_path: Path
    ) -> None:
        cfg = _make_config(tmp_path)
        planner = RecordingProvider(
            side_effect=lambda call: DispatchResult(
                module="planner",
                success=True,
                output=_plan_json(
                    _task_payload(1, "backend", "Implement API"),
                    _task_payload(2, "frontend", "Build UI"),
                ),
                event_count=2,
            )
        )
        mock_create_provider.return_value = planner

        plan = generate_plan("Ship auth", cfg)

        assert [task.module for task in plan.tasks] == ["backend", "frontend"]
        assert planner.dispatch_calls[0]["module"] == "planner"
        prompt = str(planner.dispatch_calls[0]["prompt"])
        assert "backend" in prompt
        assert "frontend" in prompt
        assert "agent_check" in prompt

    @patch("lindy_orchestrator.orchestrator.remove_worktree")
    @patch("lindy_orchestrator.orchestrator._dispatch_loop")
    @patch("lindy_orchestrator.orchestrator.create_worktree")
    def test_execute_plan_uses_distinct_worktrees_per_task(
        self,
        mock_create_worktree,
        mock_dispatch_loop,
        mock_remove_worktree,
        tmp_path: Path,
    ) -> None:
        from lindy_orchestrator.orchestrator import _execute_single_task_inner

        cfg = _make_config(tmp_path)
        worktrees = []
        for task_id, module in ((1, "backend"), (2, "frontend")):
            worktree = tmp_path / f"worktree-{task_id}"
            (worktree / module).mkdir(parents=True, exist_ok=True)
            worktrees.append(worktree)
        mock_create_worktree.side_effect = worktrees
        mock_remove_worktree.return_value = None
        seen_worktrees: list[Path | None] = []
        mock_dispatch_loop.side_effect = (
            lambda task, config, logger, progress, detail, max_retries, hooks, branch_name, worktree_path, command_queue=None: (
                (
                    seen_worktrees.append(worktree_path),
                    1,
                )[1]
            )
        )

        tasks = [
            TaskSpec(id=1, module="backend", description="Write API", skip_qa=True),
            TaskSpec(id=2, module="frontend", description="Write UI", skip_qa=True),
        ]

        for task in tasks:
            _execute_single_task_inner(
                task,
                cfg,
                _make_logger(tmp_path),
                MagicMock(),
                MagicMock(),
                1,
                None,
            )

        assert seen_worktrees == worktrees

    @patch("lindy_orchestrator.orchestrator._execute_single_task")
    @patch("lindy_orchestrator.orchestrator.create_provider")
    def test_execute_plan_parallel_ready_tasks_overlap_with_barrier(
        self, mock_create_provider, mock_execute_single_task, tmp_path: Path
    ) -> None:
        from lindy_orchestrator.orchestrator import execute_plan

        cfg = _make_config(tmp_path)
        mock_create_provider.return_value = MagicMock(validate=MagicMock())
        barrier = threading.Barrier(2)
        lock = threading.Lock()
        active = 0
        max_active = 0

        def execute_with_barrier(*args, **kwargs) -> int:
            nonlocal active, max_active
            with lock:
                active += 1
                max_active = max(max_active, active)
            barrier.wait(timeout=2)
            time.sleep(0.05)
            with lock:
                active -= 1
            return 1

        mock_execute_single_task.side_effect = execute_with_barrier

        plan = TaskPlan(
            goal="parallel barrier",
            tasks=[
                TaskSpec(id=1, module="backend", description="Task A", skip_qa=True),
                TaskSpec(id=2, module="frontend", description="Task B", skip_qa=True),
            ],
        )

        execute_plan(plan, cfg, _make_logger(tmp_path))

        assert max_active == 2

    def test_streaming_dispatch_drains_command_queue_and_reports_result(self) -> None:
        cfg = OrchestratorConfig()
        cfg.dispatcher.timeout_seconds = 5
        proc = _FakeProcess(
            [
                json.dumps({"type": "function_call", "name": "shell"}) + "\n",
                json.dumps({"msg": {"type": "agent_message", "message": "queue drained"}}) + "\n",
            ]
        )
        seen_events: list[dict] = []

        result = streaming_dispatch(
            module="backend",
            proc=proc,
            config=cfg.dispatcher,
            extract_result_from_lines=lambda _lines: "fallback",
            on_event=seen_events.append,
        )

        assert result.success is True
        assert result.output == "queue drained"
        assert result.last_tool_use == "shell"
        assert result.event_count == 2
        assert len(seen_events) == 2

    def test_dispatch_loop_retries_after_retryable_eval_failure(self, tmp_path: Path) -> None:
        from lindy_orchestrator.orchestrator import _handle_retry

        task = TaskSpec(
            id=1,
            module="backend",
            description="Implement API",
            prompt="Ship the backend API",
        )
        task.qa_results = [
            QAResult(
                gate="command_check", passed=False, output="FAILED tests/test_api.py::test_create"
            )
        ]
        hooks = HookRegistry()
        events: list[Event] = []
        hooks.on_any(events.append)

        should_continue = _handle_retry(
            task,
            "Ship the backend API",
            2,
            _make_logger(tmp_path),
            MagicMock(),
            hooks,
        )

        assert should_continue is True
        assert task.retries == 1
        retry_events = [event for event in events if event.type == EventType.TASK_RETRYING]
        assert len(retry_events) == 1

    def test_dispatch_loop_stops_after_max_retries(self, tmp_path: Path) -> None:
        from lindy_orchestrator.orchestrator import _handle_retry

        task = TaskSpec(
            id=1,
            module="backend",
            description="Implement API",
            prompt="Ship the backend API",
        )
        task.retries = 1
        task.qa_results = [QAResult(gate="command_check", passed=False, output="FAILED one")]

        should_continue = _handle_retry(
            task,
            "Ship the backend API",
            1,
            _make_logger(tmp_path),
            MagicMock(),
            None,
        )

        assert should_continue is False
        assert task.status == TaskStatus.FAILED
        assert task.retries == 2

    def test_dispatch_loop_skips_retry_for_non_retryable_eval_failure(self, tmp_path: Path) -> None:
        from lindy_orchestrator.orchestrator import _handle_retry

        task = TaskSpec(
            id=1,
            module="backend",
            description="Implement API",
            prompt="Ship the backend API",
        )
        task.qa_results = [
            QAResult(
                gate="command_check",
                passed=False,
                output="Pre-existing failure",
                retryable=False,
            )
        ]

        should_continue = _handle_retry(
            task,
            "Ship the backend API",
            2,
            _make_logger(tmp_path),
            MagicMock(),
            None,
        )

        assert should_continue is False
        assert task.status == TaskStatus.FAILED
        assert task.retries == 0

    def test_dispatch_loop_passes_eval_feedback_back_into_generator_prompt(
        self, tmp_path: Path
    ) -> None:
        from lindy_orchestrator.orchestrator import _handle_retry

        task = TaskSpec(
            id=1,
            module="backend",
            description="Implement API",
            prompt="Implement the backend API",
        )
        task.qa_results = [
            QAResult(
                gate="command_check",
                passed=False,
                output="FAILED tests/test_api.py::test_create - AssertionError: assert 200 == 201",
            )
        ]

        should_continue = _handle_retry(
            task,
            "Implement the backend API",
            2,
            _make_logger(tmp_path),
            MagicMock(),
            None,
        )

        assert should_continue is True
        assert task.retries == 1
        assert len(task.feedback_history) == 1
        assert task.feedback_history[0]["retry"] == 1
        assert task.feedback_history[0]["summary"] == "QA failed"

    @patch("lindy_orchestrator.orchestrator.create_worktree", return_value=None)
    @patch("lindy_orchestrator.orchestrator.create_provider")
    def test_execute_plan_emits_checkpoint_after_each_task_completion(
        self, mock_create_provider, _mock_create_worktree, tmp_path: Path
    ) -> None:
        from lindy_orchestrator.orchestrator import execute_plan

        cfg = _make_config(tmp_path)
        mock_create_provider.return_value = RecordingProvider()
        session_mgr = SessionManager(tmp_path / ".orchestrator" / "sessions")
        session = session_mgr.create(goal="checkpoint goal")
        hooks = HookRegistry()
        events: list[Event] = []
        hooks.on_any(events.append)

        plan = TaskPlan(
            goal="checkpoint goal",
            tasks=[
                TaskSpec(id=1, module="backend", description="Task 1", skip_qa=True),
                TaskSpec(id=2, module="frontend", description="Task 2", skip_qa=True),
            ],
        )

        execute_plan(
            plan, cfg, _make_logger(tmp_path), hooks=hooks, session_mgr=session_mgr, session=session
        )

        checkpoint_events = [event for event in events if event.type == EventType.CHECKPOINT_SAVED]
        assert session.checkpoint_count == 2
        assert len(checkpoint_events) == 2

    def test_plan_checkpoint_round_trip_restores_next_ready_task(self, tmp_path: Path) -> None:
        from lindy_orchestrator.models import plan_from_dict, plan_to_dict

        session_mgr = SessionManager(tmp_path / ".orchestrator" / "sessions")
        session = session_mgr.create(goal="resume goal")
        plan = TaskPlan(
            goal="resume goal",
            tasks=[
                TaskSpec(id=1, module="backend", description="Done", status=TaskStatus.COMPLETED),
                TaskSpec(id=2, module="frontend", description="Next", depends_on=[1]),
            ],
        )

        session_mgr.checkpoint(session, plan_to_dict(plan))
        restored = session_mgr.load_latest()
        restored_plan = plan_from_dict(restored.plan_json)

        assert restored_plan.next_ready()[0].id == 2

    @_SKIP_SSE
    def test_web_dashboard_sse_init_snapshot_contains_plan_state(self) -> None:
        hooks = HookRegistry()
        plan = TaskPlan(
            goal="dashboard goal", tasks=[TaskSpec(id=1, module="backend", description="Ship")]
        )
        port = _free_port()
        dashboard = WebDashboard(plan, hooks, port=port)
        dashboard.start()

        try:
            with closing(_open_sse(port)) as response:
                event_name, payload = _read_sse_event(response)
                assert event_name == "init"
                assert payload["goal"] == "dashboard goal"
                assert payload["tasks"][0]["module"] == "backend"
        finally:
            dashboard.stop()

    @_SKIP_SSE
    def test_web_dashboard_sse_streams_hook_events(self) -> None:
        hooks = HookRegistry()
        plan = TaskPlan(
            goal="dashboard goal", tasks=[TaskSpec(id=1, module="backend", description="Ship")]
        )
        port = _free_port()
        dashboard = WebDashboard(plan, hooks, port=port)
        dashboard.start()

        try:
            with closing(_open_sse(port)) as response:
                _read_sse_event(response)  # consume init event
                hooks.emit(Event(type=EventType.TASK_STARTED, task_id=1, module="backend"))
                time.sleep(0.15)  # allow event to propagate through queue in CI
                event_name, payload = _read_sse_event(response)
                assert event_name == "hook"
                assert payload["type"] == "task_started"
                assert payload["task_id"] == 1
        finally:
            dashboard.stop()

    @_SKIP_SSE
    def test_web_dashboard_sse_fans_out_events_to_multiple_clients(self) -> None:
        hooks = HookRegistry()
        plan = TaskPlan(
            goal="dashboard goal", tasks=[TaskSpec(id=1, module="backend", description="Ship")]
        )
        port = _free_port()
        dashboard = WebDashboard(plan, hooks, port=port)
        dashboard.start()

        try:
            with closing(_open_sse(port)) as first, closing(_open_sse(port)) as second:
                _read_sse_event(first)
                _read_sse_event(second)
                hooks.emit(Event(type=EventType.TASK_COMPLETED, task_id=1, module="backend"))
                time.sleep(0.15)  # allow event to propagate through queue in CI
                first_event = _read_sse_event(first)
                second_event = _read_sse_event(second)
                assert first_event[1]["type"] == "task_completed"
                assert second_event[1]["type"] == "task_completed"
        finally:
            dashboard.stop()

    @patch("lindy_orchestrator.providers.create_provider")
    def test_agent_check_dispatches_to_qa_role_module(
        self, mock_create_provider, tmp_path: Path
    ) -> None:
        cfg = _make_config(tmp_path, include_qa=True)
        evaluator = RecordingProvider(
            side_effect=lambda call: DispatchResult(
                module=str(call["module"]),
                success=True,
                output="QA_RESULT: PASS",
                duration_seconds=0.1,
            )
        )
        mock_create_provider.return_value = evaluator

        result = AgentCheckGate().check(
            params={"description": "Review the backend changes"},
            project_root=tmp_path,
            task_output="Generator output",
            dispatcher_config=cfg.dispatcher,
            qa_module=cfg.qa_module(),
        )

        assert result.passed is True
        assert evaluator.dispatch_calls[0]["module"] == "reviewer"
        assert Path(evaluator.dispatch_calls[0]["working_dir"]) == (tmp_path / "qa").resolve()

    @patch("lindy_orchestrator.generator_runner.create_provider")
    @patch("lindy_orchestrator.planner_runner.create_provider")
    def test_pipeline_can_use_distinct_provider_instances_per_role(
        self,
        mock_planner_create_provider,
        mock_generator_create_provider,
        tmp_path: Path,
    ) -> None:
        from lindy_orchestrator.orchestrator import _dispatch_loop

        cfg = _make_config(tmp_path, include_qa=True)
        planner_provider = RecordingProvider(
            side_effect=lambda call: DispatchResult(
                module="planner",
                success=True,
                output=_plan_json(
                    _task_payload(
                        1,
                        "backend",
                        "Implement API",
                        qa_checks=[{"gate": "agent_check", "params": {"description": "Review"}}],
                    )
                ),
            )
        )
        generator_provider = RecordingProvider(
            side_effect=lambda call: DispatchResult(
                module=str(call["module"]),
                success=True,
                output="generator done",
                duration_seconds=0.1,
            )
        )
        mock_planner_create_provider.return_value = planner_provider
        mock_generator_create_provider.return_value = generator_provider

        plan = generate_plan("Ship auth", cfg)
        task = plan.tasks[0]
        task.prompt = "Implement API"
        task.skip_qa = True
        _dispatch_loop(
            task,
            cfg,
            _make_logger(tmp_path),
            MagicMock(),
            MagicMock(),
            1,
            None,
            "af/task-1",
            None,
            None,
        )

        assert planner_provider.dispatch_calls[0]["module"] == "planner"
        assert generator_provider.dispatch_calls[0]["module"] == "backend"

    @patch("lindy_orchestrator.orchestrator.run_qa_gate")
    def test_evaluator_uses_worktree_module_path_for_context_isolation(
        self, mock_run_qa_gate, tmp_path: Path
    ) -> None:
        from lindy_orchestrator.orchestrator import _run_qa_gates

        cfg = _make_config(tmp_path)
        worktree = tmp_path / "isolated-worktree"
        (worktree / "backend").mkdir(parents=True, exist_ok=True)
        (worktree / "frontend").mkdir(parents=True, exist_ok=True)

        captured: dict[str, Path] = {}

        def capture_qa(**kwargs) -> QAResult:
            captured["project_root"] = kwargs["project_root"]
            captured["module_path"] = kwargs["module_path"]
            return QAResult(gate="command_check", passed=True, output="ok")

        mock_run_qa_gate.side_effect = capture_qa
        task = TaskSpec(
            id=1,
            module="backend",
            description="Implement API",
            qa_checks=[QACheck(gate="command_check", params={"command": "pytest"})],
        )
        task.result = "generator output"

        all_passed = _run_qa_gates(
            task,
            cfg,
            _make_logger(tmp_path),
            worktree,
            (worktree / "backend").resolve(),
            MagicMock(),
            MagicMock(),
            None,
        )

        assert all_passed is True
        assert captured["project_root"] == worktree
        assert captured["module_path"] == (worktree / "backend").resolve()
