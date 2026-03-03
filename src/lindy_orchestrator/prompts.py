"""Prompt templates for planning, dispatch, and reporting.

All templates are generic — no domain-specific content.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


PLAN_PROMPT_TEMPLATE = """\
You are the Project Orchestrator for "{project_name}".

You coordinate the following modules:
{module_list}

## Current Module Status
{module_statuses}
{architecture_section}
## Goal
{goal}

## Your Task

Decompose this goal into an ordered list of module-level tasks.
Output ONLY valid JSON (no markdown, no explanation) in this exact format:

```json
{{
  "tasks": [
    {{
      "id": 1,
      "module": "<module-name>",
      "description": "Short description of what to do",
      "prompt": "Detailed prompt for the module agent (must be self-contained)",
      "depends_on": [],
      "qa_checks": [
        {{"gate": "<gate-name>", "params": {{}}}}
      ]
    }}
  ]
}}
```

## Rules

### Branch-Based Delivery
Every task MUST instruct the agent to:
1. Create a branch named `{branch_prefix}/task-{{id}}`
2. Commit all changes to this branch
3. Push the branch: `git push origin {branch_prefix}/task-{{id}}`

Include this instruction in every task prompt.

### Task Prompts
Each task prompt SHOULD be a structured JSON object (preferred) or a plain string:

**Structured format (preferred):**
```json
{{
  "objective": "What to achieve (1-2 sentences)",
  "context_files": ["files the agent should read first"],
  "constraints": ["what NOT to change", "which libraries to use"],
  "verification": ["command to run", "expected outcome"]
}}
```

**Rules for prompts:**
- Start every prompt with "Read your STATUS.md first." (if the module has one)
- Agents must EXECUTE, not just plan. If a task requires running tests, say: "Run the tests and verify they pass."
- Keep prompts concise. The agent has full codebase access — don't repeat file contents
- Include at least one verification step so the agent can self-check before committing

### Dependencies
- Use depends_on to enforce ordering. Example: task 2 depends on task 1 → "depends_on": [1]
- Tasks with no dependencies can run in parallel
- If no explicit dependencies exist, infer a reasonable sequential order

### Fullstack Tasks
- For changes that span multiple modules (e.g. backend API + frontend UI), use `"module": "root"` to work from the project root
- Prefer fullstack tasks over splitting tightly-coupled changes into separate module tasks
- Only split into per-module tasks when the changes are truly independent

### QA Checks
Available gates:
{available_gates}

Use ONE primary QA check per task. For code changes with CI configured, prefer `ci_check`.

Today's date: {date}
"""


REPORT_PROMPT_TEMPLATE = """\
You are the Project Orchestrator. Generate a concise completion report.

## Goal
{goal}

## Execution Results
{results}

Generate a structured report. If all tasks passed, use this format:

GOAL COMPLETED: <goal>

## Summary
- <module>: <what was accomplished>

## Quality Gates
- <check>: PASS/FAIL (details)

## Next Steps
1. <suggested follow-up>

If any task failed, use:

GOAL PAUSED: <goal>

## Completed Tasks
- Task N: <description> (PASS)

## Failed Task
- Task N: <description>
- Failure: <what went wrong>

## Recommended Action
<what to do next>
"""


def render_plan_prompt(
    goal: str,
    module_summaries: dict[str, str],
    project_name: str = "project",
    branch_prefix: str = "af",
    modules: list[dict[str, Any]] | None = None,
    available_gates: list[str] | None = None,
    architecture: str | None = None,
) -> str:
    """Render the planning prompt with current context."""
    # Build module list
    module_lines = []
    if modules:
        for m in modules:
            module_lines.append(f"- **{m['name']}/** ({m.get('path', m['name'] + '/')})")
    else:
        for name in module_summaries:
            module_lines.append(f"- **{name}/**")
    module_list = "\n".join(module_lines)

    # Build status section
    status_parts = []
    for name, summary in module_summaries.items():
        status_parts.append(f"### {name}\n{summary}")
    module_statuses = "\n\n".join(status_parts)

    # Build gate list
    gate_lines = []
    if available_gates:
        for g in available_gates:
            gate_lines.append(f"- `{g}`")
    else:
        gate_lines = [
            "- `ci_check`: Polls GitHub Actions CI status on the pushed branch. "
            'Params: {{"repo": "org/repo", "branch": "<branch>"}}',
            "- `command_check`: Runs a shell command and checks exit code. "
            'Params: {{"command": "<cmd>", "cwd": "<dir>"}}',
            "- `agent_check`: Dispatches the QA module agent for complex validation. "
            'Params: {{"description": "<what to validate>"}}',
        ]
    gates_text = "\n".join(gate_lines)

    # Build architecture section if provided
    if architecture:
        architecture_section = (
            "\n## Architecture (respect these boundaries)\n\n" + architecture + "\n\n"
        )
    else:
        architecture_section = ""

    return PLAN_PROMPT_TEMPLATE.format(
        project_name=project_name,
        module_list=module_list,
        module_statuses=module_statuses,
        architecture_section=architecture_section,
        goal=goal,
        branch_prefix=branch_prefix,
        available_gates=gates_text,
        date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
    )


def render_report_prompt(goal: str, task_results: list[dict[str, Any]]) -> str:
    """Render the report generation prompt."""
    result_lines = []
    for t in task_results:
        qa_summary = t.get("qa_summary", "none")
        result_lines.append(
            f"Task {t['id']} [{t['module']}]: {t['description']}\n"
            f"  Status: {t['status']}\n"
            f"  QA: {qa_summary}\n"
            f"  Output preview: {t.get('result_preview', 'N/A')}"
        )

    return REPORT_PROMPT_TEMPLATE.format(
        goal=goal,
        results="\n\n".join(result_lines),
    )
