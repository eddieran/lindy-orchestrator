"""Generator role runner."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Callable

from .config import GeneratorConfig, OrchestratorConfig
from .hooks import Event, EventType, HookRegistry
from .models import EvalFeedback, GeneratorOutput, TaskSpec
from .providers import create_provider


def _read_if_exists(path: Path) -> str:
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


class GeneratorRunner:
    """Execute the generator role for a task."""

    def __init__(self, config: GeneratorConfig, project_config: OrchestratorConfig) -> None:
        self.config = config
        self.project_config = project_config

    def _build_prompt(
        self,
        task: TaskSpec,
        worktree: Path,
        branch_name: str,
        feedback: EvalFeedback | None,
    ) -> str:
        provider_dir = "codex" if self.config.provider == "codex_cli" else "claude"
        orch_dir = self.project_config.root / ".orchestrator"
        root_instructions = _read_if_exists(orch_dir / provider_dir / "root.md")
        module_instructions = _read_if_exists(orch_dir / provider_dir / f"{task.module}.md")
        status_text = _read_if_exists(orch_dir / "status" / f"{task.module}.md")

        parts = [self.config.prompt_prefix.strip()]
        if root_instructions or module_instructions:
            header = "CODEX.md" if provider_dir == "codex" else "CLAUDE.md"
            parts.append(
                f"## {header} Instructions\n\n"
                + "\n\n".join(p for p in [root_instructions, module_instructions] if p)
            )
        if status_text:
            parts.append(f"## Current STATUS.md\n\n{status_text}")

        generator_prompt = task.generator_prompt or task.prompt or task.description
        parts.append(generator_prompt)
        parts.append(
            "## Branch Delivery\n\n"
            f"You are already on branch `{branch_name}` in `{worktree}`.\n"
            "Do not switch branches. Commit and push your work to this branch when done."
        )

        if feedback is not None:
            feedback_parts = [feedback.summary]
            if feedback.failed_criteria:
                feedback_parts.append(
                    "Failed criteria:\n"
                    + "\n".join(f"- {item}" for item in feedback.failed_criteria)
                )
            if feedback.specific_errors:
                feedback_parts.append(
                    "Specific errors:\n"
                    + "\n".join(f"- {item}" for item in feedback.specific_errors)
                )
            if feedback.files_to_check:
                feedback_parts.append(
                    "Files to check:\n" + "\n".join(f"- {item}" for item in feedback.files_to_check)
                )
            if feedback.remediation_steps:
                feedback_parts.append(
                    "Remediation steps:\n"
                    + "\n".join(f"- {item}" for item in feedback.remediation_steps)
                )
            if feedback.missing_behaviors:
                feedback_parts.append(
                    "Missing behaviors:\n"
                    + "\n".join(f"- {item}" for item in feedback.missing_behaviors)
                )
            if feedback.evidence:
                feedback_parts.append(f"Evidence:\n{feedback.evidence}")
            parts.append("## Retry Feedback\n\n" + "\n\n".join(p for p in feedback_parts if p))

        return "\n\n".join(part for part in parts if part)

    def execute(
        self,
        task: TaskSpec,
        worktree: Path,
        branch_name: str,
        feedback: EvalFeedback | None = None,
        on_event: Callable[[dict[str, Any]], None] | None = None,
        hooks: HookRegistry | None = None,
    ) -> GeneratorOutput:
        prompt = self._build_prompt(task, worktree, branch_name, feedback)
        provider = create_provider(self.config)
        dispatch_on_event = on_event
        if hooks is not None:

            def _dispatch_on_event(event: dict[str, Any]) -> None:
                hooks.emit(
                    Event(
                        type=EventType.AGENT_EVENT,
                        task_id=task.id,
                        module=task.module,
                        data={"payload": event},
                    )
                )
                if on_event is not None:
                    on_event(event)

            dispatch_on_event = _dispatch_on_event

        result = provider.dispatch(
            module=task.module,
            working_dir=worktree,
            prompt=prompt,
            on_event=dispatch_on_event,
            stall_seconds=task.stall_seconds or self.config.stall_timeout,
        )

        if hooks is not None:
            hooks.emit(
                Event(
                    type=EventType.AGENT_OUTPUT,
                    task_id=task.id,
                    module=task.module,
                    data={
                        "output": result.output,
                        "success": result.success,
                        "truncated": result.truncated,
                        "event_count": result.event_count,
                        "last_tool": result.last_tool_use,
                    },
                )
            )

        diff, diff_source = _capture_git_diff(worktree)
        if hooks is not None:
            hooks.emit(
                Event(
                    type=EventType.GIT_DIFF_CAPTURED,
                    task_id=task.id,
                    module=task.module,
                    data={"diff": diff, "source": diff_source},
                )
            )

        return GeneratorOutput(
            success=result.success,
            output=result.output,
            diff=diff,
            cost_usd=result.cost_usd,
            duration_seconds=result.duration_seconds,
            event_count=result.event_count,
            last_tool=result.last_tool_use,
        )


def _capture_git_diff(worktree: Path) -> tuple[str, str]:
    for command, source in (
        (["git", "log", "-1", "-p"], "git log -1 -p"),
        (["git", "diff", "HEAD"], "git diff HEAD"),
    ):
        try:
            proc = subprocess.run(
                command,
                cwd=worktree,
                capture_output=True,
                text=True,
                timeout=30,
            )
        except Exception:
            continue

        if proc.stdout.strip():
            return proc.stdout, source

    return "", "git diff HEAD"
