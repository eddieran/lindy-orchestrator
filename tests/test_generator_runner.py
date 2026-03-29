from __future__ import annotations

from pathlib import Path

from lindy_orchestrator.config import GeneratorConfig, OrchestratorConfig
from lindy_orchestrator.generator_runner import GeneratorRunner
from lindy_orchestrator.hooks import EventType, HookRegistry
from lindy_orchestrator.models import DispatchResult, EvalFeedback, TaskSpec


class _FakeProvider:
    def __init__(self) -> None:
        self.prompt = ""

    def dispatch(self, module, working_dir, prompt, on_event=None, stall_seconds=None):
        self.prompt = prompt
        return DispatchResult(
            module=module,
            success=True,
            output="generator output",
            duration_seconds=1.2,
            event_count=3,
            last_tool_use="write_file",
            cost_usd=0.42,
        )


def test_build_prompt_isolates_generator_context(tmp_path: Path) -> None:
    orch = tmp_path / ".orchestrator"
    (orch / "codex").mkdir(parents=True)
    (orch / "status").mkdir(parents=True)
    (orch / "codex" / "root.md").write_text("ROOT CODEX")
    (orch / "codex" / "backend.md").write_text("MODULE CODEX")
    (orch / "status" / "backend.md").write_text("STATUS BODY")

    cfg = OrchestratorConfig(
        generator=GeneratorConfig(provider="codex_cli", prompt_prefix="PREFIX")
    )
    cfg._config_dir = tmp_path

    task = TaskSpec(
        id=1,
        module="backend",
        description="desc",
        generator_prompt="IMPLEMENT FEATURE",
        acceptance_criteria="ACCEPTANCE TEXT",
        evaluator_prompt="EVALUATOR TEXT",
    )

    runner = GeneratorRunner(cfg.generator, cfg)
    prompt = runner._build_prompt(task, tmp_path, "af/task-1", None)

    assert "PREFIX" in prompt
    assert "IMPLEMENT FEATURE" in prompt
    assert "ROOT CODEX" in prompt
    assert "MODULE CODEX" in prompt
    assert "STATUS BODY" in prompt
    assert "ACCEPTANCE TEXT" not in prompt
    assert "EVALUATOR TEXT" not in prompt


def test_execute_collects_diff_and_retry_feedback(tmp_path: Path, monkeypatch) -> None:
    cfg = OrchestratorConfig(generator=GeneratorConfig())
    cfg._config_dir = tmp_path
    task = TaskSpec(id=1, module="root", description="desc", generator_prompt="IMPLEMENT")
    provider = _FakeProvider()

    monkeypatch.setattr("lindy_orchestrator.generator_runner.create_provider", lambda _: provider)

    class _Proc:
        stdout = "diff --git a/file b/file"

    monkeypatch.setattr(
        "lindy_orchestrator.generator_runner.subprocess.run", lambda *a, **k: _Proc()
    )

    runner = GeneratorRunner(cfg.generator, cfg)
    result = runner.execute(
        task,
        tmp_path,
        "af/task-1",
        feedback=EvalFeedback(summary="fix bugs", failed_criteria=["criterion"]),
    )

    assert result.success is True
    assert result.diff == "diff --git a/file b/file"
    assert result.last_tool == "write_file"
    assert "fix bugs" in provider.prompt
    assert "criterion" in provider.prompt


def test_execute_emits_agent_events_and_prefers_git_log_diff(tmp_path: Path, monkeypatch) -> None:
    cfg = OrchestratorConfig(generator=GeneratorConfig())
    cfg._config_dir = tmp_path
    task = TaskSpec(id=7, module="root", description="desc", generator_prompt="IMPLEMENT")
    provider = _FakeProvider()

    raw_event = {"type": "function_call", "name": "shell", "arguments": {"cmd": "echo hi"}}

    def _dispatch(module, working_dir, prompt, on_event=None, stall_seconds=None):
        if on_event is not None:
            on_event(raw_event)
        return DispatchResult(
            module=module,
            success=True,
            output="generator output",
            duration_seconds=1.2,
            event_count=3,
            last_tool_use="write_file",
            cost_usd=0.42,
        )

    provider.dispatch = _dispatch  # type: ignore[method-assign]
    monkeypatch.setattr("lindy_orchestrator.generator_runner.create_provider", lambda _: provider)

    class _Proc:
        stdout = "commit diff"

    monkeypatch.setattr(
        "lindy_orchestrator.generator_runner.subprocess.run", lambda *a, **k: _Proc()
    )

    hooks = HookRegistry()
    seen: list[tuple[EventType, dict]] = []
    hooks.on_any(lambda event: seen.append((event.type, event.data)))
    forwarded_events: list[dict] = []

    runner = GeneratorRunner(cfg.generator, cfg)
    result = runner.execute(
        task,
        tmp_path,
        "af/task-1",
        on_event=forwarded_events.append,
        hooks=hooks,
    )

    assert result.diff == "commit diff"
    assert forwarded_events == [raw_event]
    assert seen == [
        (EventType.AGENT_EVENT, {"payload": raw_event}),
        (
            EventType.AGENT_OUTPUT,
            {
                "output": "generator output",
                "success": True,
                "truncated": False,
                "event_count": 3,
                "last_tool": "write_file",
            },
        ),
        (
            EventType.GIT_DIFF_CAPTURED,
            {"diff": "commit diff", "source": "git log -1 -p"},
        ),
    ]


def test_execute_falls_back_to_git_diff_head_when_git_log_is_empty(
    tmp_path: Path, monkeypatch
) -> None:
    cfg = OrchestratorConfig(generator=GeneratorConfig())
    cfg._config_dir = tmp_path
    task = TaskSpec(id=1, module="root", description="desc", generator_prompt="IMPLEMENT")
    provider = _FakeProvider()

    monkeypatch.setattr("lindy_orchestrator.generator_runner.create_provider", lambda _: provider)

    class _Proc:
        def __init__(self, stdout: str) -> None:
            self.stdout = stdout

    calls: list[list[str]] = []

    def _run(command, *args, **kwargs):
        calls.append(command)
        if command == ["git", "log", "-1", "-p"]:
            return _Proc("")
        return _Proc("fallback diff")

    monkeypatch.setattr("lindy_orchestrator.generator_runner.subprocess.run", _run)

    hooks = HookRegistry()
    seen: list[tuple[EventType, dict]] = []
    hooks.on_any(lambda event: seen.append((event.type, event.data)))

    runner = GeneratorRunner(cfg.generator, cfg)
    result = runner.execute(task, tmp_path, "af/task-1", hooks=hooks)

    assert result.diff == "fallback diff"
    assert calls == [["git", "log", "-1", "-p"], ["git", "diff", "HEAD"]]
    assert seen[-1] == (
        EventType.GIT_DIFF_CAPTURED,
        {"diff": "fallback diff", "source": "git diff HEAD"},
    )
