"""CLI entry point for lindy-orchestrate."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

from . import __version__
from .cli_helpers import (
    finalise_session,
    load_cfg,
    make_on_progress,
    persist_plan,
    plan_from_dict,
    plan_to_dict,
    print_task_list,
    resolve_goal,
    validate_provider,
)
from .dag import truncate_goal
from .dashboard import Dashboard
from .hooks import HookRegistry
from .logger import ActionLogger
from .models import TaskStatus
from .reporter import (
    PlanProgress,
    generate_execution_summary,
    save_summary_report,
)
from .session import SessionManager


def _version_callback(value: bool) -> None:
    if value:
        print(f"lindy-orchestrate {__version__}")
        raise typer.Exit()


app = typer.Typer(
    name="lindy-orchestrate",
    help="Lightweight, git-native multi-agent orchestration framework.",
    no_args_is_help=True,
)
console = Console()


@app.callback(invoke_without_command=True)
def main(
    version: bool = typer.Option(
        False,
        "--version",
        "-V",
        callback=_version_callback,
        is_eager=True,
        help="Show version and exit.",
    ),
) -> None:
    """Lightweight, git-native multi-agent orchestration framework."""


@app.command()
def run(
    goal: Optional[str] = typer.Argument(None, help="Natural language goal to achieve"),
    file: Optional[str] = typer.Option(
        None, "-f", "--file", help="Read goal from file (use '-' for stdin)"
    ),
    plan_file: Optional[str] = typer.Option(
        None, "-p", "--plan", help="Execute a saved plan JSON (skip planning step)"
    ),
    config: Optional[str] = typer.Option(None, "-c", "--config", help="Config YAML path"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Read and analyze only"),
    verbose: bool = typer.Option(False, "-v", "--verbose", help="Show detailed output"),
    provider: Optional[str] = typer.Option(
        None,
        "--provider",
        help="Dispatch provider: claude_cli (default) or codex_cli",
    ),
) -> None:
    """Execute a goal with full orchestration.

    Goal can be provided as argument, from a file (--file goal.md), or stdin (--file -).
    Use --plan to execute a previously saved plan JSON directly (skips LLM planning).
    """
    from .scheduler import execute_plan

    cfg = load_cfg(config)
    if dry_run:
        cfg.safety.dry_run = True

    # Override provider from CLI flag if specified
    if provider:
        cfg.dispatcher.provider = provider
    validate_provider(cfg.dispatcher.provider)

    # Load plan from file or generate from goal
    if plan_file:
        plan_path = Path(plan_file)
        if not plan_path.exists():
            console.print(f"[red]Plan file not found: {plan_file}[/]")
            raise typer.Exit(1)
        plan_data = json.loads(plan_path.read_text(encoding="utf-8"))
        plan = plan_from_dict(plan_data)
        goal = plan.goal
        console.print(f"[bold]lindy-orchestrate v{__version__}[/]")
        console.print(f"Goal: [bold]{truncate_goal(goal)}[/]")
        console.print(f"[green]Loaded plan from {plan_file}[/]\n")
    else:
        from .planner import generate_plan

        goal = resolve_goal(goal, file)

        console.print(f"[bold]lindy-orchestrate v{__version__}[/]")
        console.print(f"Goal: [bold]{truncate_goal(goal)}[/]")

    logger = ActionLogger(cfg.log_path)
    sessions = SessionManager(cfg.sessions_path)
    session = sessions.create(goal=goal)
    console.print(f"Session: {session.session_id}\n")

    start = time.time()  # wall-clock time (monotonic skips macOS sleep)
    on_progress = make_on_progress(console)

    logger.log_action("session_start", details={"goal": goal, "dry_run": cfg.safety.dry_run})

    if not plan_file:
        # Step 1: Plan
        console.print("[bold cyan][1/3][/] Generating task plan...")
        progress = PlanProgress(console=console)
        progress.start()

        try:
            plan = generate_plan(goal, cfg, on_progress=on_progress, progress=progress)
        except Exception as e:
            progress.stop(f"Planning failed: {e}")
            console.print(f"[red]Planning failed: {e}[/]")
            session.status = "failed"
            sessions.save(session)
            raise typer.Exit(1)
        finally:
            if progress._live is not None:
                progress.stop()

    # Persist plan to session for resume capability
    session.plan_json = plan_to_dict(plan)
    sessions.save(session)

    # Auto-persist plan to .orchestrator/plans/
    persist_plan(cfg.root, plan)

    print_task_list(console, plan.tasks)

    # Step 3: Execute
    console.print("\n[bold cyan][2/3][/] Executing tasks...")
    hooks = HookRegistry()
    dashboard: Dashboard | None = None
    if console.is_terminal:
        dashboard = Dashboard(plan, hooks, console=console, verbose=verbose)
        dashboard.start()
        # Dashboard takes over display; suppress on_progress text output
        plan = execute_plan(plan, cfg, logger, on_progress=None, verbose=False, hooks=hooks)
        dashboard.stop()
    else:
        plan = execute_plan(
            plan, cfg, logger, on_progress=on_progress, verbose=verbose, hooks=hooks
        )
    hooks.shutdown()

    # Step 4: Report
    console.print("\n[bold cyan][3/3][/] Generating report...")
    duration = round(time.time() - start, 1)

    generate_execution_summary(plan, duration, session.session_id, console=console)
    report_path = save_summary_report(plan, duration, session.session_id, cfg.root)
    console.print(f"\n[dim]Report saved to {report_path}[/]")

    completed, failed = finalise_session(session, sessions, plan)

    logger.log_action(
        "session_end",
        details={
            "duration_seconds": duration,
            "completed": len(completed),
            "failed": len(failed),
        },
    )


@app.command()
def plan(
    goal: Optional[str] = typer.Argument(None, help="Natural language goal to decompose"),
    file: Optional[str] = typer.Option(
        None, "-f", "--file", help="Read goal from file (use '-' for stdin)"
    ),
    config: Optional[str] = typer.Option(None, "-c", "--config"),
    output_file: Optional[str] = typer.Option(None, "-o", "--output", help="Save plan as JSON"),
) -> None:
    """Generate a task plan without executing it.

    Goal can be provided as argument, from a file (--file goal.md), or stdin (--file -).
    """
    from .planner import generate_plan

    goal = resolve_goal(goal, file)
    cfg = load_cfg(config)

    console.print(f"[bold]lindy-orchestrate v{__version__}[/]")
    console.print(f"Goal: [bold]{truncate_goal(goal)}[/]\n")

    progress = PlanProgress(console=console)
    on_progress = make_on_progress(console)

    progress.start()
    try:
        plan_result = generate_plan(goal, cfg, on_progress=on_progress, progress=progress)
    finally:
        if progress._live is not None:
            progress.stop()

    print_task_list(console, plan_result.tasks, show_qa=True, show_prompt=True)

    # Auto-persist plan
    plan_json_path = persist_plan(cfg.root, plan_result)
    console.print(f"\n[green]Plan saved to {plan_json_path}[/]")
    console.print(f"[dim]To execute: lindy-orchestrate run --plan {plan_json_path}[/]")

    if output_file:
        data = plan_to_dict(plan_result)
        Path(output_file).write_text(json.dumps(data, indent=2, default=str))
        console.print(f"[green]Also saved to {output_file}[/]")


@app.command()
def resume(
    session_id: Optional[str] = typer.Argument(None, help="Session ID to resume"),
    config: Optional[str] = typer.Option(None, "-c", "--config"),
    verbose: bool = typer.Option(False, "-v", "--verbose", help="Show detailed output"),
) -> None:
    """Resume a previous session from its last checkpoint.

    Skips already-completed tasks and re-executes failed/pending ones.
    """
    from .scheduler import execute_plan

    cfg = load_cfg(config)
    sessions = SessionManager(cfg.sessions_path)

    if session_id:
        session = sessions.load(session_id)
    else:
        session = sessions.load_latest()

    if not session:
        console.print("[red]No session found to resume.[/]")
        raise typer.Exit(1)

    console.print(f"[bold]lindy-orchestrate v{__version__}[/] — Resume")
    console.print(f"Session: [bold]{session.session_id}[/]")
    console.print(f"Goal: {truncate_goal(session.goal)}")
    console.print(f"Status: {session.status}")

    if session.status == "completed":
        console.print("[yellow]Session already completed. Nothing to resume.[/]")
        return

    if not session.plan_json:
        console.print("[yellow]No saved plan found. Re-running from scratch...[/]")
        run(goal=session.goal, config=config, dry_run=False, verbose=verbose)
        return

    # Restore plan from checkpoint
    plan = plan_from_dict(session.plan_json)
    completed_count = sum(1 for t in plan.tasks if t.status.value == "completed")
    remaining = [t for t in plan.tasks if t.status.value not in ("completed", "skipped")]

    console.print(
        f"\n  [bold]{completed_count}[/] tasks already completed, "
        f"[bold]{len(remaining)}[/] remaining"
    )

    # Reset FAILED tasks to PENDING for retry
    for t in plan.tasks:
        if t.status == TaskStatus.FAILED:
            t.status = TaskStatus.PENDING
            t.retries = 0
            t.qa_results = []
            console.print(f"    {t.id}. [bold][{t.module}][/] {t.description} [yellow]→ retry[/]")

    # Reset SKIPPED tasks whose dependencies are no longer failed
    # (they were skipped because a dep failed, but that dep is now reset to PENDING)
    changed = True
    while changed:
        changed = False
        failed_or_skipped = {
            t.id for t in plan.tasks if t.status in (TaskStatus.FAILED, TaskStatus.SKIPPED)
        }
        for t in plan.tasks:
            if t.status != TaskStatus.SKIPPED:
                continue
            # If none of its deps are still failed/skipped, reset to PENDING
            if not any(dep in failed_or_skipped for dep in t.depends_on):
                t.status = TaskStatus.PENDING
                t.result = ""
                changed = True
                console.print(
                    f"    {t.id}. [bold][{t.module}][/] {t.description} [yellow]→ unskipped[/]"
                )

    for t in plan.tasks:
        if t.status == TaskStatus.PENDING:
            console.print(f"    {t.id}. [bold][{t.module}][/] {t.description} [dim]pending[/]")

    # Execute remaining
    logger = ActionLogger(cfg.log_path)
    start = time.time()  # wall-clock time (monotonic skips macOS sleep)
    on_progress = make_on_progress(console)

    console.print("\n[bold cyan]Resuming execution...[/]")
    hooks = HookRegistry()
    dashboard: Dashboard | None = None
    if console.is_terminal:
        dashboard = Dashboard(plan, hooks, console=console, verbose=verbose)
        dashboard.start()
        plan = execute_plan(plan, cfg, logger, on_progress=None, verbose=False, hooks=hooks)
        dashboard.stop()
    else:
        plan = execute_plan(
            plan, cfg, logger, on_progress=on_progress, verbose=verbose, hooks=hooks
        )
    hooks.shutdown()

    duration = round(time.time() - start, 1)

    generate_execution_summary(plan, duration, session.session_id, console=console)
    report_path = save_summary_report(plan, duration, session.session_id, cfg.root)
    console.print(f"\n[dim]Report saved to {report_path}[/]")

    finalise_session(session, sessions, plan)


@app.command()
def version(
    as_json: bool = typer.Option(False, "--json", help="Output as JSON for scripting"),
) -> None:
    """Print the current lindy-orchestrator version."""
    if as_json:
        console.print_json(json.dumps({"version": __version__}))
    else:
        console.print(f"lindy-orchestrator v{__version__}")


from .cli_clear import register_clear_command  # noqa: E402
from .cli_config import register_config_commands  # noqa: E402
from .cli_ext import register_ext_commands  # noqa: E402
from .cli_onboard import register_onboard_command  # noqa: E402
from .cli_status import register_status_commands  # noqa: E402

register_config_commands(app, console)
register_ext_commands(app, console)
register_onboard_command(app, console)
register_clear_command(app, console)
register_status_commands(app, console, load_cfg)
