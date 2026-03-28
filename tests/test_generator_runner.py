"""Tests for generator dispatch prompt isolation."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from lindy_orchestrator.config import GeneratorConfig, OrchestratorConfig
from lindy_orchestrator.generator_runner import GeneratorRunner
from lindy_orchestrator.models import DispatchResult, TaskSpec, TaskStatus
from lindy_orchestrator.qa.feedback import FailureCategory, StructuredFeedback, build_retry_prompt


def _make_config(tmp_path: Path) -> OrchestratorConfig:
    cfg = OrchestratorConfig(generator=GeneratorConfig(provider="codex_cli"))
    cfg._config_dir = tmp_path

    orch = tmp_path / ".orchestrator"
    (orch / "status").mkdir(parents=True, exist_ok=True)
    (orch / "codex").mkdir(parents=True, exist_ok=True)
    (orch / "claude").mkdir(parents=True, exist_ok=True)

    (orch / "status" / "root.md").write_text("status context")
    (orch / "codex" / "root.md").write_text("codex root instructions")
    (orch / "claude" / "root.md").write_text("claude root instructions")
    return cfg


def test_build_prompt_uses_generator_context_only(tmp_path: Path) -> None:
    cfg = _make_config(tmp_path)
    runner = GeneratorRunner(cfg)
    progress = MagicMock()
    task = TaskSpec(
        id=1,
        module="root",
        description="test",
        prompt="legacy prompt should stay hidden",
        generator_prompt="safe generator prompt",
        evaluator_prompt="secret evaluator instructions",
        acceptance_criteria=["must not leak"],
    )

    prompt = runner.build_prompt(task, "feature/test", None, 0, progress)

    assert "safe generator prompt" in prompt
    assert "legacy prompt should stay hidden" not in prompt
    assert "secret evaluator instructions" not in prompt
    assert "must not leak" not in prompt
    assert "status context" in prompt
    assert "codex root instructions" in prompt
    assert "claude root instructions" not in prompt
    assert "git checkout -b feature/test" in prompt
    assert "CODEX.md" in prompt


def test_retry_prompt_remains_isolated(tmp_path: Path) -> None:
    cfg = _make_config(tmp_path)
    runner = GeneratorRunner(cfg)
    progress = MagicMock()
    task = TaskSpec(
        id=1,
        module="root",
        description="test",
        generator_prompt="safe generator prompt",
        evaluator_prompt="secret evaluator instructions",
        acceptance_criteria=["must not leak"],
    )
    feedback = StructuredFeedback(
        category=FailureCategory.TEST_FAILURE,
        summary="Fix the failing assertion",
        specific_errors=["AssertionError: expected 2, got 1"],
        remediation_steps=["Update the implementation"],
        files_to_check=["src/app.py"],
        retry_number=1,
    )
    task.prompt = build_retry_prompt(runner.generator_prompt(task), [feedback], 1, 2)

    prompt = runner.build_prompt(task, "feature/test", None, 1, progress)

    assert "safe generator prompt" in prompt
    assert "Fix the failing assertion" in prompt
    assert "secret evaluator instructions" not in prompt
    assert "must not leak" not in prompt
    assert "status context" not in prompt
    assert "codex root instructions" not in prompt


@patch("lindy_orchestrator.generator_runner.create_provider")
def test_dispatch_loop_sends_isolated_prompt(
    mock_create_provider: MagicMock,
    tmp_path: Path,
) -> None:
    from lindy_orchestrator.orchestrator import _dispatch_loop

    captured_prompts: list[str] = []

    def _dispatch(**kwargs):
        captured_prompts.append(kwargs["prompt"])
        return DispatchResult(
            module="root",
            success=True,
            output="done",
            duration_seconds=1.0,
            event_count=1,
        )

    mock_provider = MagicMock()
    mock_provider.dispatch.side_effect = _dispatch
    mock_create_provider.return_value = mock_provider

    cfg = _make_config(tmp_path)
    task = TaskSpec(
        id=1,
        module="root",
        description="test",
        prompt="legacy prompt should stay hidden",
        generator_prompt="safe generator prompt",
        evaluator_prompt="secret evaluator instructions",
        acceptance_criteria=["must not leak"],
        skip_qa=True,
        status=TaskStatus.PENDING,
    )

    dispatches = _dispatch_loop(
        task=task,
        config=cfg,
        logger=MagicMock(),
        progress=MagicMock(),
        detail=MagicMock(),
        max_retries=2,
        hooks=None,
        branch_name="feature/test",
        worktree_path=None,
    )

    assert dispatches == 1
    assert len(captured_prompts) == 1
    assert "safe generator prompt" in captured_prompts[0]
    assert "secret evaluator instructions" not in captured_prompts[0]
    assert "must not leak" not in captured_prompts[0]
    assert "codex root instructions" in captured_prompts[0]
