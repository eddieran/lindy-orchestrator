"""Tests for evaluator runner QA orchestration and prompt isolation."""

from __future__ import annotations

from concurrent.futures import Future
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from lindy_orchestrator.config import EvaluatorConfig, ModuleConfig, OrchestratorConfig
from lindy_orchestrator.evaluator_runner import EvaluatorRunner
from lindy_orchestrator.models import DispatchResult, GeneratorOutput, QACheck, QAResult, TaskSpec


def _task(**overrides: object) -> TaskSpec:
    data: dict[str, object] = {
        "id": 1,
        "module": "backend",
        "description": "Evaluate task",
        "generator_prompt": "generator_prompt_text",
        "acceptance_criteria": "acceptance_criteria_text",
        "evaluator_prompt": "Look for edge cases",
    }
    data.update(overrides)
    return TaskSpec(**data)


def _project_config(tmp_path: Path) -> OrchestratorConfig:
    config = OrchestratorConfig(
        modules=[ModuleConfig(name="backend", path="backend/")],
        evaluator=EvaluatorConfig(provider="codex_cli", pass_threshold=80, prompt_prefix="Prefix"),
    )
    config._config_dir = tmp_path
    return config


def _generator_output(**overrides: object) -> GeneratorOutput:
    data: dict[str, object] = {
        "success": True,
        "output": "Generated output body",
        "diff": "diff --git a/app.py b/app.py\n+print('hello')",
    }
    data.update(overrides)
    return GeneratorOutput(**data)


def test_build_eval_prompt_contains_acceptance_criteria_not_generator_prompt(tmp_path: Path):
    runner = EvaluatorRunner(EvaluatorConfig(prompt_prefix="Prefix"), _project_config(tmp_path))
    prompt = runner._build_eval_prompt(
        _task(),
        _generator_output(),
        [QAResult(gate="pytest", passed=True, output="all good")],
    )

    assert "acceptance_criteria_text" in prompt
    assert "generator_prompt_text" not in prompt
    assert "Look for edge cases" in prompt
    assert "Score 90-100" in prompt


def test_run_qa_gates_uses_thread_pool_executor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    runner = EvaluatorRunner(EvaluatorConfig(), _project_config(tmp_path))
    checks = [QACheck(gate="a"), QACheck(gate="b")]
    submitted: list[str] = []
    worker_counts: list[int] = []

    class FakeExecutor:
        def __init__(self, max_workers: int):
            worker_counts.append(max_workers)

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def submit(self, fn, check):  # noqa: ANN001
            submitted.append(check.gate)
            future: Future[QAResult] = Future()
            future.set_result(fn(check))
            return future

    def fake_run_qa_gate(**kwargs):  # noqa: ANN001
        return QAResult(gate=kwargs["check"].gate, passed=True, output="ok")

    monkeypatch.setattr("lindy_orchestrator.evaluator_runner.ThreadPoolExecutor", FakeExecutor)
    monkeypatch.setattr("lindy_orchestrator.evaluator_runner.run_qa_gate", fake_run_qa_gate)

    results = runner._run_qa_gates(
        checks=checks,
        worktree=tmp_path,
        project_root=tmp_path,
        module_name="backend",
        module_path=tmp_path / "backend",
    )

    assert submitted == ["a", "b"]
    assert worker_counts == [2]
    assert [result.gate for result in results] == ["a", "b"]


def test_skip_gates_filters_selected_checks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    runner = EvaluatorRunner(EvaluatorConfig(), _project_config(tmp_path))
    checks = [QACheck(gate="lint"), QACheck(gate="pytest")]
    called: list[str] = []

    def fake_run_qa_gate(**kwargs):  # noqa: ANN001
        called.append(kwargs["check"].gate)
        return QAResult(gate=kwargs["check"].gate, passed=True, output="ok")

    monkeypatch.setattr("lindy_orchestrator.evaluator_runner.run_qa_gate", fake_run_qa_gate)

    results = runner._run_qa_gates(
        checks=checks,
        worktree=tmp_path,
        project_root=tmp_path,
        module_name="backend",
        module_path=tmp_path / "backend",
        skip_gates=["lint"],
    )

    assert called == ["pytest"]
    assert [result.gate for result in results] == ["pytest"]


@patch("lindy_orchestrator.evaluator_runner.create_provider")
def test_score_below_threshold_fails(mock_create_provider: MagicMock, tmp_path: Path):
    provider = MagicMock()
    provider.dispatch_simple.return_value = DispatchResult(
        module="backend",
        success=True,
        output='{"score": 45, "feedback": {"summary": "not enough"}}',
    )
    mock_create_provider.return_value = provider
    runner = EvaluatorRunner(EvaluatorConfig(pass_threshold=80), _project_config(tmp_path))

    result = runner.evaluate(_task(), _generator_output(), tmp_path)

    assert result.score == 45
    assert result.passed is False


@patch("lindy_orchestrator.evaluator_runner.create_provider")
def test_score_above_threshold_passes(mock_create_provider: MagicMock, tmp_path: Path):
    provider = MagicMock()
    provider.dispatch_simple.return_value = DispatchResult(
        module="backend",
        success=True,
        output='{"score": 85, "feedback": {"summary": "solid"}}',
    )
    mock_create_provider.return_value = provider
    runner = EvaluatorRunner(EvaluatorConfig(pass_threshold=80), _project_config(tmp_path))

    result = runner.evaluate(_task(), _generator_output(), tmp_path)

    assert result.score == 85
    assert result.passed is True


@patch("lindy_orchestrator.evaluator_runner.create_provider")
def test_passed_is_computed_in_code_not_model_boolean(
    mock_create_provider: MagicMock, tmp_path: Path
):
    provider = MagicMock()
    provider.dispatch_simple.return_value = DispatchResult(
        module="backend",
        success=True,
        output='{"score": 85, "passed": false, "feedback": {"summary": "solid"}}',
    )
    mock_create_provider.return_value = provider
    runner = EvaluatorRunner(EvaluatorConfig(pass_threshold=80), _project_config(tmp_path))

    result = runner.evaluate(_task(), _generator_output(), tmp_path)

    assert result.passed is True


@patch("lindy_orchestrator.evaluator_runner.create_provider")
def test_all_non_retryable_qa_failures_skip_evaluator(
    mock_create_provider: MagicMock, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    runner = EvaluatorRunner(EvaluatorConfig(), _project_config(tmp_path))
    monkeypatch.setattr(
        runner,
        "_run_qa_gates",
        lambda **kwargs: [  # noqa: ARG005
            QAResult(gate="pytest", passed=False, output="pre-existing", retryable=False),
            QAResult(gate="lint", passed=False, output="pre-existing", retryable=False),
        ],
    )

    result = runner.evaluate(
        _task(qa_checks=[QACheck(gate="pytest")]), _generator_output(), tmp_path
    )

    assert result.retryable is False
    assert result.passed is False
    assert result.score == 0
    assert "non-retryable" in result.feedback.summary.lower()
    mock_create_provider.assert_not_called()


@patch("lindy_orchestrator.evaluator_runner.create_provider")
def test_json_parse_failure_returns_retryable_zero(mock_create_provider: MagicMock, tmp_path: Path):
    provider = MagicMock()
    provider.dispatch_simple.return_value = DispatchResult(
        module="backend",
        success=True,
        output="not valid json",
    )
    mock_create_provider.return_value = provider
    runner = EvaluatorRunner(EvaluatorConfig(), _project_config(tmp_path))

    result = runner.evaluate(_task(), _generator_output(), tmp_path)

    assert result.score == 0
    assert result.retryable is True
    assert "parse evaluator output" in result.feedback.summary.lower()


@patch("lindy_orchestrator.evaluator_runner.create_provider")
def test_timeout_returns_retryable_zero(mock_create_provider: MagicMock, tmp_path: Path):
    provider = MagicMock()
    provider.dispatch_simple.side_effect = TimeoutError("slow")
    mock_create_provider.return_value = provider
    runner = EvaluatorRunner(EvaluatorConfig(timeout_seconds=123), _project_config(tmp_path))

    result = runner.evaluate(_task(), _generator_output(), tmp_path)

    assert result.score == 0
    assert result.retryable is True
    assert "123s" in result.feedback.summary


@patch("lindy_orchestrator.evaluator_runner.create_provider")
def test_skip_qa_returns_immediate_pass(mock_create_provider: MagicMock, tmp_path: Path):
    runner = EvaluatorRunner(EvaluatorConfig(), _project_config(tmp_path))

    result = runner.evaluate(_task(skip_qa=True), _generator_output(), tmp_path)

    assert result.score == 100
    assert result.passed is True
    assert result.retryable is False
    mock_create_provider.assert_not_called()


@patch("lindy_orchestrator.evaluator_runner.create_provider")
def test_provider_created_from_evaluator_config(mock_create_provider: MagicMock, tmp_path: Path):
    provider = MagicMock()
    provider.dispatch_simple.return_value = DispatchResult(
        module="backend",
        success=True,
        output='{"score": 85, "feedback": {"summary": "solid"}}',
    )
    mock_create_provider.return_value = provider
    config = EvaluatorConfig(provider="codex_cli", pass_threshold=80)
    runner = EvaluatorRunner(config, _project_config(tmp_path))

    runner.evaluate(_task(), _generator_output(), tmp_path)

    mock_create_provider.assert_called_once_with(config)


def test_prompt_includes_qa_summary_and_diff(tmp_path: Path):
    runner = EvaluatorRunner(EvaluatorConfig(), _project_config(tmp_path))
    prompt = runner._build_eval_prompt(
        _task(),
        _generator_output(output="final output text", diff="x" * 20),
        [QAResult(gate="pytest", passed=False, output="3 tests failed", retryable=True)],
    )

    assert "3 tests failed" in prompt
    assert "final output text" in prompt
    assert "xxxxxxxxxxxxxxxxxxxx" in prompt
