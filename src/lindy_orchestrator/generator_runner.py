"""Generator role runner."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Callable

from .config import GeneratorConfig, OrchestratorConfig
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
    ) -> GeneratorOutput:
        prompt = self._build_prompt(task, worktree, branch_name, feedback)
        provider = create_provider(self.config)
        result = provider.dispatch(
            module=task.module,
            working_dir=worktree,
            prompt=prompt,
            on_event=on_event,
            stall_seconds=task.stall_seconds or self.config.stall_timeout,
        )

        diff = ""
        try:
            proc = subprocess.run(
                ["git", "diff", "HEAD"],
                cwd=worktree,
                capture_output=True,
                text=True,
                timeout=30,
            )
            diff = proc.stdout
        except Exception:
            diff = ""

        return GeneratorOutput(
            success=result.success,
            output=result.output,
            prompt=prompt,
            diff=diff,
            cost_usd=result.cost_usd,
            duration_seconds=result.duration_seconds,
            event_count=result.event_count,
            last_tool=result.last_tool_use,
        )
