"""Agent check gate: dispatches a QA module agent for complex validation."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..models import QAResult
from . import register


@register("agent_check")
class AgentCheckGate:
    """Dispatches the QA module agent for validation that can't be automated
    with a simple command.

    params:
        description: str — what to validate
        + any additional context params
    """

    def check(
        self,
        params: dict[str, Any],
        project_root: Path,
        module_name: str = "",
        task_output: str = "",
        dispatcher_config=None,
        qa_module=None,
        **kwargs,
    ) -> QAResult:
        description = params.get("description", "Validate task output")

        if qa_module is None:
            return QAResult(
                gate="agent_check",
                passed=False,
                output="No QA module configured (set role: qa on a module in orchestrator.yaml)",
            )

        if dispatcher_config is None:
            return QAResult(
                gate="agent_check",
                passed=False,
                output="No dispatcher config available",
            )

        # Build QA prompt
        prompt = (
            "Read your STATUS.md first.\n\n"
            f"## Task: {description}\n\n"
            "Steps:\n"
            "1. Analyze the relevant files and outputs\n"
            "2. Verify the validation criteria\n"
            "3. Print your result as: QA_RESULT: PASS or QA_RESULT: FAIL\n"
            "4. If FAIL, print: FAILURE_REASON: <description>\n"
        )

        # Add task output context if available
        if task_output:
            preview = task_output[:2000]
            prompt += f"\n## Agent Output (for reference)\n```\n{preview}\n```\n"

        # Dispatch to QA module
        from ..providers import create_provider

        qa_path = (project_root / qa_module.path).resolve()
        try:
            provider = create_provider(dispatcher_config)
            result = provider.dispatch(
                module=qa_module.name,
                working_dir=qa_path,
                prompt=prompt,
            )
        except Exception as e:
            return QAResult(
                gate="agent_check",
                passed=False,
                output=f"QA agent dispatch failed: {e}",
            )

        # Parse QA agent output for QA_RESULT: PASS/FAIL
        passed = "QA_RESULT: PASS" in result.output
        output = result.output[:2000] if result.output else "No output"

        return QAResult(
            gate="agent_check",
            passed=passed,
            output=output,
            details={
                "description": description,
                "agent_success": result.success,
                "duration": result.duration_seconds,
            },
        )
