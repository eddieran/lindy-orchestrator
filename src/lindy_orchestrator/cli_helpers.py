"""Shared helpers for CLI commands."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

import typer
from rich.console import Console

from .config import CONFIG_FILENAME, OrchestratorConfig, load_config
from .models import TaskPlan

console = Console()


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
        p = Path(file)
        if not p.exists():
            console.print(f"[red]File not found: {file}[/]")
            raise typer.Exit(1)
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
