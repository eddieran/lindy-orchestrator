"""Shared helpers for CLI commands."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

import typer
from rich.console import Console

from typing import Callable

from .console import console

from .config import (
    CONFIG_FILENAME,
    DispatcherConfig,
    OrchestratorConfig,
    load_config,
    load_global_config,
)
from .models import TaskSpec, TaskPlan
from .providers import create_provider
from .session import SessionManager, SessionState

# Max tasks to display fully; above this, collapse the middle
MAX_DISPLAY_TASKS = 8
# When collapsed: show first N and last N
_COLLAPSE_HEAD = 3
_COLLAPSE_TAIL = 2

def require_path(path: str | Path, label: str = "File") -> Path:
    """Validate that *path* exists, or print a red error and exit.

    Returns the resolved ``Path`` on success.
    """
    p = Path(path)
    if not p.exists():
        console.print(f"[red]{label} not found: {path}[/]")
        raise typer.Exit(1)
    return p


def make_on_progress(con: Console) -> Callable[[str], None]:
    """Create an on_progress callback that prints to the given console."""

    def on_progress(msg: str) -> None:
        con.print(msg)

    return on_progress


def resolve_goal(goal: str | None, file: str | None) -> str:
    """Resolve goal text from argument, file, or stdin."""
    import sys

    if file:
        if file == "-":
            text = sys.stdin.read().strip()
            if not text:
                console.print("[red]No input received from stdin.[/]")
                raise typer.Exit(1)
            return text
        p = require_path(file)
        return p.read_text(encoding="utf-8").strip()
    if goal:
        return goal
    console.print("[red]Provide a goal as argument or use --file/-f.[/]")
    raise typer.Exit(1)


def load_cfg(config_path: str | None) -> OrchestratorConfig:
    """Load config with error handling."""
    try:
        return load_config(config_path)
    except FileNotFoundError as e:
        console.print(f"[red]{e}[/]")
        console.print(f"Run `lindy-orchestrate init` to create {CONFIG_FILENAME}")
        raise typer.Exit(1)
    except Exception as e:
        console.print(f"[red]Config error: {e}[/]")
        raise typer.Exit(1)


def plan_to_dict(plan: TaskPlan) -> dict:
    """Serialize a TaskPlan to a JSON-safe dict."""
    from .models import plan_to_dict as _plan_to_dict

    return _plan_to_dict(plan)


def plan_from_dict(data: dict) -> TaskPlan:
    """Deserialize a TaskPlan from a dict."""
    from .models import plan_from_dict as _plan_from_dict

    return _plan_from_dict(data)


def print_task_list(
    con: Console,
    tasks: list[TaskSpec],
    *,
    show_qa: bool = False,
    show_prompt: bool = False,
) -> None:
    """Print a task list, collapsing the middle when there are many tasks.

    Args:
        con: Rich console to print to.
        tasks: List of tasks to display.
        show_qa: Show QA gates per task (used by `plan` command).
        show_prompt: Show prompt preview per task (used by `plan` command).
    """
    total = len(tasks)
    con.print(f"\n  [bold]{total} tasks planned:[/]")

    def _print_task(t: TaskSpec) -> None:
        deps = f" [dim](depends on: {t.depends_on})[/]" if t.depends_on else ""
        con.print(f"    {t.id}. [bold][{t.module}][/] {t.description}{deps}")
        if show_qa:
            qa = ", ".join(q.gate for q in t.qa_checks) if t.qa_checks else "none"
            con.print(f"       QA: {qa}")
        if show_prompt and t.prompt:
            con.print(f"       Prompt: {t.prompt[:100]}...")

    if total <= MAX_DISPLAY_TASKS:
        for t in tasks:
            _print_task(t)
    else:
        for t in tasks[:_COLLAPSE_HEAD]:
            _print_task(t)
        hidden = total - _COLLAPSE_HEAD - _COLLAPSE_TAIL
        con.print(f"    [dim]... {hidden} more tasks ...[/]")
        for t in tasks[-_COLLAPSE_TAIL:]:
            _print_task(t)


def validate_provider(provider_name: str | None = None) -> str:
    """Validate the dispatch provider is available.

    Resolution order:
      1. provider_name argument (CLI --provider flag)
      2. ~/.lindy/config.yaml global config
      3. Built-in default: claude_cli

    Returns:
        The resolved provider name.

    Raises:
        typer.Exit: If the provider binary is not found.
    """
    name = provider_name or load_global_config().provider
    try:
        provider = create_provider(DispatcherConfig(provider=name))
        if hasattr(provider, "validate"):
            provider.validate()
    except RuntimeError as e:
        console.print(f"[red]Error: {e}[/]")
        raise typer.Exit(1)
    except ValueError as e:
        console.print(f"[red]{e}[/]")
        raise typer.Exit(1)
    return name


def persist_plan(root: Path, plan: TaskPlan) -> Path:
    """Auto-save plan to .orchestrator/plans/. Returns the JSON path."""
    plans_dir = root / ".orchestrator" / "plans"
    plans_dir.mkdir(parents=True, exist_ok=True)

    slug = re.sub(r"[^a-z0-9]+", "-", plan.goal.lower().strip())[:50].strip("-")
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    filename = f"{ts}-{slug}" if slug else ts

    json_path = plans_dir / f"{filename}.json"
    json_path.write_text(json.dumps(plan_to_dict(plan), indent=2, default=str))

    md_lines = [f"# Plan: {plan.goal}\n"]
    for t in plan.tasks:
        deps = f" (depends on: {t.depends_on})" if t.depends_on else ""
        qa = ", ".join(q.gate for q in t.qa_checks) or "none"
        md_lines.append(f"## Task {t.id}: [{t.module}] {t.description}{deps}")
        md_lines.append(f"- **Status**: {t.status.value}")
        md_lines.append(f"- **QA**: {qa}")
        if t.prompt:
            preview = t.prompt[:200].replace("\n", " ")
            md_lines.append(f"- **Prompt**: {preview}...")
        md_lines.append("")

    (plans_dir / "latest.md").write_text("\n".join(md_lines))
    return json_path


def finalise_session(
    session: SessionState,
    sessions: SessionManager,
    plan: TaskPlan,
) -> tuple[list, list]:
    """Save final plan state to session and mark completed or paused.

    Returns (completed_tasks, failed_tasks) for caller use.
    """
    completed = [t for t in plan.tasks if t.status.value == "completed"]
    failed = [t for t in plan.tasks if t.status.value == "failed"]

    session.plan_json = plan_to_dict(plan)
    session.completed_tasks = [
        {"id": t.id, "module": t.module, "description": t.description} for t in completed
    ]
    if failed:
        session.status = "paused"
        sessions.save(session)
    else:
        sessions.complete(session)

    return completed, failed
