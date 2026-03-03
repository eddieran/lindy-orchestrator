"""Goal decomposition: Goal → TaskPlan via LLM.

Supports two modes:
- CLI: Uses `claude -p` subprocess (no API key needed)
- API: Uses Anthropic SDK directly (requires ANTHROPIC_API_KEY)
"""

from __future__ import annotations

import json
import re
from typing import Callable

import time

from .config import OrchestratorConfig
from .dispatcher import dispatch_agent
from .models import PlannerMode, QACheck, TaskItem, TaskPlan, TaskStatus
from .prompts import render_plan_prompt
from .status.parser import parse_status_md


def generate_plan(
    goal: str,
    config: OrchestratorConfig,
    on_progress: Callable[[str], None] | None = None,
) -> TaskPlan:
    """Generate a task plan from a natural-language goal.

    Reads all module statuses, builds context, and calls LLM to decompose.
    """
    # Step 1: Read all module statuses
    statuses = _read_all_statuses(config)
    if on_progress:
        for name, summary in statuses.items():
            on_progress(
                f"  [dim]{name}:[/] {summary.splitlines()[0] if summary.splitlines() else 'empty'}"
            )

    # Step 2: Build prompt
    modules_info = [{"name": m.name, "path": m.path} for m in config.modules]

    # Read ARCHITECTURE.md if it exists
    arch_path = config.root / "ARCHITECTURE.md"
    architecture = arch_path.read_text(encoding="utf-8") if arch_path.exists() else None

    # Collect available gates
    gate_names = ["ci_check", "command_check", "agent_check"]
    for cg in config.qa_gates.custom:
        gate_names.append(cg.name)

    prompt = render_plan_prompt(
        goal=goal,
        module_summaries=statuses,
        project_name=config.project.name,
        branch_prefix=config.project.branch_prefix,
        modules=modules_info,
        available_gates=gate_names,
        architecture=architecture,
    )

    # Step 3: Call LLM
    if config.safety.dry_run:
        return TaskPlan(
            goal=goal,
            tasks=[
                TaskItem(
                    id=1,
                    module=config.modules[0].name if config.modules else "default",
                    description="[DRY RUN] Would decompose goal into tasks",
                    prompt="[DRY RUN]",
                )
            ],
        )

    mode = PlannerMode(config.planner.mode)
    if mode == PlannerMode.API:
        output = _plan_via_api(prompt, config)
    else:
        output = _plan_via_cli(prompt, config, on_progress=on_progress)

    # Step 4: Parse JSON output into TaskPlan
    return _parse_task_plan(goal, output)


def _read_all_statuses(config: OrchestratorConfig) -> dict[str, str]:
    """Read all module STATUS.md files and build concise summaries."""
    statuses = {}
    for mod in config.modules:
        path = config.status_path(mod.name)
        if path.exists():
            status = parse_status_md(path)
            open_reqs = [r for r in status.requests if r.status.upper() == "OPEN"]
            parts = [
                f"Health: {status.meta.overall_health}",
                f"Active work: {len(status.active_work)} tasks",
                f"Completed recently: {len(status.completed)} tasks",
            ]
            if open_reqs:
                parts.append(f"Open requests: {len(open_reqs)}")
                for r in open_reqs:
                    parts.append(f"  - {r.id}: {r.request}")
            if status.blockers:
                parts.append(f"BLOCKERS: {status.blockers}")
            if status.key_metrics:
                parts.append("Key metrics:")
                for k, v in list(status.key_metrics.items())[:10]:
                    parts.append(f"  - {k}: {v}")
            statuses[mod.name] = "\n".join(parts)
        else:
            statuses[mod.name] = "(STATUS.md not found)"
    return statuses


def _plan_via_cli(
    prompt: str,
    config: OrchestratorConfig,
    on_progress: Callable[[str], None] | None = None,
) -> str:
    """Call claude -p for planning with heartbeat feedback."""

    def progress(msg: str) -> None:
        if on_progress:
            on_progress(msg)

    # Heartbeat state
    _hb_count = 0
    _hb_start = time.monotonic()
    _hb_last_print = _hb_start

    def _on_event(event: dict) -> None:
        nonlocal _hb_count, _hb_last_print
        _hb_count += 1
        now = time.monotonic()
        if now - _hb_last_print >= 30:
            elapsed = int(now - _hb_start)
            mins, secs = divmod(elapsed, 60)
            progress(f"  [dim]⋯ planning: {_hb_count} events, {mins}m{secs:02d}s[/]")
            _hb_last_print = now

    progress("  [dim]Generating plan...[/]")
    result = dispatch_agent(
        module="planner",
        working_dir=config.root,
        prompt=prompt,
        config=config.dispatcher,
        on_event=_on_event,
    )
    if not result.success:
        raise RuntimeError(f"Planning failed: {result.output[:500]}")

    progress(f"  [dim]Plan generated ({result.duration_seconds}s, {result.event_count} events)[/]")
    return result.output


def _plan_via_api(prompt: str, config: OrchestratorConfig) -> str:
    """Call Anthropic API for planning."""
    try:
        import anthropic
    except ImportError:
        raise ImportError(
            "anthropic package not installed. Install with: pip install lindy-orchestrator[api]"
        )

    client = anthropic.Anthropic()
    response = client.messages.create(
        model=config.planner.model,
        max_tokens=config.planner.max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text


def _parse_task_plan(goal: str, output: str) -> TaskPlan:
    """Parse JSON task plan from LLM output."""
    try:
        json_match = re.search(r"\{[\s\S]*\}", output)
        if json_match:
            data = json.loads(json_match.group())
        else:
            data = json.loads(output)
    except json.JSONDecodeError:
        return TaskPlan(
            goal=goal,
            tasks=[
                TaskItem(
                    id=1,
                    module="unknown",
                    description=f"Failed to parse task plan JSON from output ({len(output)} chars)",
                    prompt="",
                    status=TaskStatus.FAILED,
                )
            ],
        )

    tasks = []
    for t in data.get("tasks", []):
        qa_checks = [
            QACheck(gate=c.get("gate", c.get("check_type", "")), params=c.get("params", {}))
            for c in t.get("qa_checks", [])
        ]
        raw_prompt = t.get("prompt", "")
        prompt = _format_prompt(raw_prompt) if isinstance(raw_prompt, dict) else raw_prompt
        tasks.append(
            TaskItem(
                id=t["id"],
                module=t.get("module", t.get("department", "unknown")),
                description=t["description"],
                prompt=prompt,
                depends_on=t.get("depends_on", []),
                qa_checks=qa_checks,
            )
        )

    # If no task has depends_on, infer sequential dependencies
    has_any_deps = any(t.depends_on for t in tasks)
    if not has_any_deps and len(tasks) > 1:
        for i in range(1, len(tasks)):
            tasks[i].depends_on = [tasks[i - 1].id]

    return TaskPlan(goal=goal, tasks=tasks)


def _format_prompt(prompt_dict: dict) -> str:
    """Format a structured prompt dict into instruction text.

    Structured format:
    {
      "objective": "What to achieve",
      "context_files": ["file1.py", "file2.py"],
      "constraints": ["do not change X", "use library Y"],
      "verification": ["run pytest", "expected: all pass"]
    }
    """
    parts: list[str] = []

    objective = prompt_dict.get("objective", "")
    if objective:
        parts.append(f"## Objective\n{objective}")

    context_files = prompt_dict.get("context_files", [])
    if context_files:
        file_list = "\n".join(f"- `{f}`" for f in context_files)
        parts.append(f"## Context Files (read these first)\n{file_list}")

    constraints = prompt_dict.get("constraints", [])
    if constraints:
        constraint_list = "\n".join(f"- {c}" for c in constraints)
        parts.append(f"## Constraints\n{constraint_list}")

    verification = prompt_dict.get("verification", [])
    if verification:
        verify_list = "\n".join(f"- {v}" for v in verification)
        parts.append(f"## Before committing, verify\n{verify_list}")

    return "\n\n".join(parts)
