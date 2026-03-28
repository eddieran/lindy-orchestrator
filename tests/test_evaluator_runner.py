from __future__ import annotations

from pathlib import Path

from lindy_orchestrator.config import EvaluatorConfig, OrchestratorConfig
from lindy_orchestrator.evaluator_runner import EvaluatorRunner
from lindy_orchestrator.models import (
    DispatchResult,
    EvalResult,
    GeneratorOutput,
    QACheck,
    QAResult,
    TaskSpec,
)


class _FakeProvider:
    def __init__(self, output: str) -> None:
        self.output = output
        self.prompt = ""

    def dispatch_simple(self, module, working_dir, prompt):
        self.prompt = prompt
        return DispatchResult(module=module, success=True, output=self.output, duration_seconds=0.4)


def test_build_eval_prompt_excludes_generator_prompt(tmp_path: Path) -> None:
    cfg = OrchestratorConfig(evaluator=EvaluatorConfig())
    cfg._config_dir = tmp_path
    task = TaskSpec(
        id=1,
        module="root",
        description="desc",
        generator_prompt="GENERATOR SECRET",
        acceptance_criteria="MUST PASS",
        evaluator_prompt="CHECK DETAILS",
    )
    runner = EvaluatorRunner(cfg.evaluator, cfg)

    prompt = runner._build_eval_prompt(
        task,
        GeneratorOutput(success=True, output="output", diff="diff"),
        [QAResult(gate="pytest", passed=False, output="boom")],
    )

    assert "MUST PASS" in prompt
    assert "CHECK DETAILS" in prompt
    assert "GENERATOR SECRET" not in prompt
    assert "Score 90-100" in prompt


def test_evaluate_returns_non_retryable_when_all_qa_failures_are_non_retryable(
    tmp_path: Path, monkeypatch
) -> None:
    cfg = OrchestratorConfig(evaluator=EvaluatorConfig())
    cfg._config_dir = tmp_path
    task = TaskSpec(id=1, module="root", description="desc", qa_checks=[QACheck(gate="pytest")])
    runner = EvaluatorRunner(cfg.evaluator, cfg)

    monkeypatch.setattr(
        runner,
        "_run_qa_gates",
        lambda **_: [QAResult(gate="pytest", passed=False, output="pre-existing", retryable=False)],
    )

    called = {"count": 0}

    def _should_not_run(*args, **kwargs):
        called["count"] += 1
        return EvalResult(score=100, passed=True)

    monkeypatch.setattr(runner, "_run_eval_agent", _should_not_run)

    result = runner.evaluate(task, GeneratorOutput(success=True, output="o", diff="d"), tmp_path)

    assert result.retryable is False
    assert result.passed is False
    assert called["count"] == 0


def test_evaluate_uses_score_threshold(tmp_path: Path, monkeypatch) -> None:
    cfg = OrchestratorConfig(evaluator=EvaluatorConfig(pass_threshold=80))
    cfg._config_dir = tmp_path
    task = TaskSpec(id=1, module="root", description="desc")
    provider = _FakeProvider('{"score": 85, "feedback": {"summary": "good"}}')
    runner = EvaluatorRunner(cfg.evaluator, cfg)

    monkeypatch.setattr("lindy_orchestrator.evaluator_runner.create_provider", lambda _: provider)
    monkeypatch.setattr(runner, "_run_qa_gates", lambda **_: [])

    result = runner.evaluate(task, GeneratorOutput(success=True, output="o", diff="d"), tmp_path)

    assert result.passed is True
    assert result.score == 85
    assert "desc" in provider.prompt
