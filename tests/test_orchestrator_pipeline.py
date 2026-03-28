from __future__ import annotations

import threading
import time
from pathlib import Path

from lindy_orchestrator.config import ModuleConfig, OrchestratorConfig, SafetyConfig
from lindy_orchestrator.hooks import EventType, HookRegistry
from lindy_orchestrator.logger import ActionLogger
from lindy_orchestrator.models import (
    EvalFeedback,
    EvalResult,
    GeneratorOutput,
    TaskPlan,
    TaskSpec,
    TaskStatus,
)
from lindy_orchestrator.orchestrator import CommandQueue, execute_plan


class _ValidateOnlyProvider:
    def validate(self) -> None:
        return None


def _config(tmp_path: Path) -> OrchestratorConfig:
    cfg = OrchestratorConfig(
        modules=[ModuleConfig(name="root", path=".")],
        safety=SafetyConfig(max_parallel=2, max_retries_per_task=2),
    )
    cfg._config_dir = tmp_path
    return cfg


def _prepare_runtime(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "lindy_orchestrator.orchestrator.create_provider", lambda _: _ValidateOnlyProvider()
    )
    monkeypatch.setattr("lindy_orchestrator.orchestrator.create_worktree", lambda *a, **k: tmp_path)
    monkeypatch.setattr("lindy_orchestrator.orchestrator.remove_worktree", lambda *a, **k: None)
    monkeypatch.setattr(
        "lindy_orchestrator.orchestrator.cleanup_all_worktrees", lambda *a, **k: None
    )
    monkeypatch.setattr(
        "lindy_orchestrator.orchestrator._check_delivery", lambda *a, **k: (True, "ok")
    )


def test_command_queue_basic_operations() -> None:
    queue = CommandQueue()
    queue.pause()
    assert queue.is_paused is True
    queue.resume()
    assert queue.is_paused is False
    queue.skip(1)
    assert queue.pop_skip(1) is True
    assert queue.pop_skip(1) is False
    queue.force_pass(2)
    assert queue.pop_force_pass(2) is True
    assert queue.pop_force_pass(2) is False


def test_execute_plan_retries_with_evaluator_feedback(tmp_path: Path, monkeypatch) -> None:
    _prepare_runtime(monkeypatch, tmp_path)
    cfg = _config(tmp_path)
    logger = ActionLogger(tmp_path / "actions.jsonl")
    plan = TaskPlan(
        goal="g",
        tasks=[TaskSpec(id=1, module="root", description="desc", generator_prompt="do work")],
    )

    feedback_seen: list[str | None] = []

    def _gen(self, task, worktree, branch_name, feedback=None, on_event=None):
        feedback_seen.append(feedback.summary if feedback else None)
        return GeneratorOutput(success=True, output="out", diff="diff")

    results = [
        EvalResult(
            score=40, passed=False, retryable=True, feedback=EvalFeedback(summary="fix tests")
        ),
        EvalResult(score=92, passed=True, retryable=True, feedback=EvalFeedback(summary="clean")),
    ]

    def _eval(self, task, gen_output, worktree):
        return results.pop(0)

    monkeypatch.setattr("lindy_orchestrator.orchestrator.GeneratorRunner.execute", _gen)
    monkeypatch.setattr("lindy_orchestrator.orchestrator.EvaluatorRunner.evaluate", _eval)

    hooks = HookRegistry()
    events = []
    hooks.on_any(lambda event: events.append((event.type, dict(event.data))))

    execute_plan(plan, cfg, logger, hooks=hooks)

    assert plan.tasks[0].status == TaskStatus.COMPLETED
    assert feedback_seen == [None, "fix tests"]
    assert [event[0] for event in events].count(EventType.EVAL_SCORED) == 2
    assert any(
        event[0] == EventType.PHASE_CHANGED and event[1]["phase"] == "generating"
        for event in events
    )
    assert any(
        event[0] == EventType.PHASE_CHANGED and event[1]["phase"] == "evaluating"
        for event in events
    )


def test_execute_plan_skip_command_prevents_execution(tmp_path: Path, monkeypatch) -> None:
    _prepare_runtime(monkeypatch, tmp_path)
    cfg = _config(tmp_path)
    logger = ActionLogger(tmp_path / "actions.jsonl")
    plan = TaskPlan(goal="g", tasks=[TaskSpec(id=1, module="root", description="desc")])
    queue = CommandQueue()
    queue.skip(1)

    def _should_not_run(*args, **kwargs):
        raise AssertionError("generator should not run for a skipped task")

    monkeypatch.setattr("lindy_orchestrator.orchestrator.GeneratorRunner.execute", _should_not_run)

    execute_plan(plan, cfg, logger, command_queue=queue)

    assert plan.tasks[0].status == TaskStatus.SKIPPED


def test_execute_plan_force_pass_completes_after_failed_eval(tmp_path: Path, monkeypatch) -> None:
    _prepare_runtime(monkeypatch, tmp_path)
    cfg = _config(tmp_path)
    logger = ActionLogger(tmp_path / "actions.jsonl")
    plan = TaskPlan(goal="g", tasks=[TaskSpec(id=1, module="root", description="desc")])
    queue = CommandQueue()
    queue.force_pass(1)
    calls = {"gen": 0}

    def _gen(self, task, worktree, branch_name, feedback=None, on_event=None):
        calls["gen"] += 1
        return GeneratorOutput(success=True, output="out", diff="diff")

    def _eval(self, task, gen_output, worktree):
        return EvalResult(
            score=10, passed=False, retryable=True, feedback=EvalFeedback(summary="bad")
        )

    monkeypatch.setattr("lindy_orchestrator.orchestrator.GeneratorRunner.execute", _gen)
    monkeypatch.setattr("lindy_orchestrator.orchestrator.EvaluatorRunner.evaluate", _eval)

    execute_plan(plan, cfg, logger, command_queue=queue)

    assert calls["gen"] == 1
    assert plan.tasks[0].status == TaskStatus.COMPLETED


def test_execute_plan_waits_while_paused(tmp_path: Path, monkeypatch) -> None:
    _prepare_runtime(monkeypatch, tmp_path)
    cfg = _config(tmp_path)
    logger = ActionLogger(tmp_path / "actions.jsonl")
    plan = TaskPlan(goal="g", tasks=[TaskSpec(id=1, module="root", description="desc")])
    queue = CommandQueue()
    queue.pause()
    started = threading.Event()

    def _gen(self, task, worktree, branch_name, feedback=None, on_event=None):
        started.set()
        return GeneratorOutput(success=True, output="out", diff="diff")

    def _eval(self, task, gen_output, worktree):
        return EvalResult(
            score=100, passed=True, retryable=True, feedback=EvalFeedback(summary="ok")
        )

    monkeypatch.setattr("lindy_orchestrator.orchestrator.GeneratorRunner.execute", _gen)
    monkeypatch.setattr("lindy_orchestrator.orchestrator.EvaluatorRunner.evaluate", _eval)

    thread = threading.Thread(
        target=execute_plan,
        args=(plan, cfg, logger),
        kwargs={"command_queue": queue},
        daemon=True,
    )
    thread.start()

    time.sleep(0.2)
    assert started.is_set() is False

    queue.resume()
    thread.join(timeout=5)

    assert started.is_set() is True
    assert plan.tasks[0].status == TaskStatus.COMPLETED
