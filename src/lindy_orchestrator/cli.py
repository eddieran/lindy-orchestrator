"""CLI entry point for lindy-orchestrate."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

from . import __version__
from .config import CONFIG_FILENAME, load_config
from .dispatcher import find_claude_cli
from .logger import ActionLogger
from .models import TaskPlan, TaskStatus
from .reporter import PlanProgress, print_goal_report, print_status_table
from .session import SessionManager
from .status.parser import parse_status_md
from .status.templates import generate_status_md

app = typer.Typer(
    name="lindy-orchestrate",
    help="Lightweight, git-native multi-agent orchestration framework.",
    no_args_is_help=True,
)
console = Console()


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------

# Markers that identify a module directory
_MODULE_MARKERS = {
    "pyproject.toml": "Python",
    "setup.py": "Python",
    "requirements.txt": "Python",
    "package.json": "Node.js",
    "Cargo.toml": "Rust",
    "go.mod": "Go",
    "pom.xml": "Java",
    "build.gradle": "Java/Kotlin",
    "CMakeLists.txt": "C/C++",
    "Makefile": "C/C++",
}


@app.command()
def init(
    modules: Optional[str] = typer.Option(
        None, "--modules", "-m", help="Comma-separated module names (skip auto-detect)"
    ),
    depth: int = typer.Option(1, "--depth", help="Directory scan depth"),
    no_status: bool = typer.Option(False, "--no-status", help="Skip STATUS.md creation"),
    force: bool = typer.Option(False, "--force", help="Overwrite existing files"),
):
    """Scaffold orchestration onto an existing project."""
    cwd = Path.cwd()
    console.print(f"[bold]lindy-orchestrate v{__version__}[/] — Initializing\n")

    # Detect or parse modules
    if modules:
        detected = [(name.strip(), name.strip()) for name in modules.split(",")]
    else:
        console.print("Scanning project structure...")
        detected = _detect_modules(cwd, depth)
        if not detected:
            console.print("[yellow]No modules detected.[/] Use --modules to specify manually.")
            raise typer.Exit(1)

    for name, tech in detected:
        console.print(f"  Found: [bold]{name}/[/] ({tech})")

    # Generate orchestrator.yaml
    config_path = cwd / CONFIG_FILENAME
    if config_path.exists() and not force:
        console.print(f"\n[yellow]{CONFIG_FILENAME} already exists.[/] Use --force to overwrite.")
    else:
        config_content = _generate_config(cwd.name, detected)
        config_path.write_text(config_content, encoding="utf-8")
        console.print(f"\n[green]Created {CONFIG_FILENAME}[/]")

    # Generate STATUS.md templates
    if not no_status:
        for name, _ in detected:
            status_path = cwd / name / "STATUS.md"
            if status_path.exists() and not force:
                console.print(f"  [dim]{name}/STATUS.md already exists, skipping[/]")
            else:
                status_path.parent.mkdir(parents=True, exist_ok=True)
                status_path.write_text(generate_status_md(name), encoding="utf-8")
                console.print(f"  [green]Created {name}/STATUS.md[/]")

    # Create .orchestrator/ directory
    orch_dir = cwd / ".orchestrator"
    (orch_dir / "logs").mkdir(parents=True, exist_ok=True)
    (orch_dir / "sessions").mkdir(parents=True, exist_ok=True)
    console.print("[green]Created .orchestrator/ directory[/]")

    # Update .gitignore
    gitignore = cwd / ".gitignore"
    ignore_entries = [".orchestrator/logs/", ".orchestrator/sessions/"]
    if gitignore.exists():
        existing = gitignore.read_text(encoding="utf-8")
        to_add = [e for e in ignore_entries if e not in existing]
        if to_add:
            with gitignore.open("a", encoding="utf-8") as f:
                f.write("\n# lindy-orchestrator\n")
                for entry in to_add:
                    f.write(f"{entry}\n")
            console.print("[green]Updated .gitignore[/]")
    else:
        gitignore.write_text(
            "# lindy-orchestrator\n" + "\n".join(ignore_entries) + "\n",
            encoding="utf-8",
        )
        console.print("[green]Created .gitignore[/]")

    console.print("\n[bold green]Done![/] Next steps:")
    console.print(f"  1. Review {CONFIG_FILENAME}")
    console.print("  2. Edit each STATUS.md with current module state")
    console.print('  3. Run: lindy-orchestrate plan "Your goal here"')


_IGNORED_DIRS = {
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    "dist",
    "build",
    ".eggs",
    "target",
    ".next",
    ".nuxt",
    ".output",
    "vendor",
    "coverage",
    "htmlcov",
    ".tox",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".terraform",
}


def _detect_modules(root: Path, max_depth: int) -> list[tuple[str, str]]:
    """Auto-detect project modules by scanning for marker files."""
    modules = []
    for item in sorted(root.iterdir()):
        if not item.is_dir() or item.name.startswith(".") or item.name in _IGNORED_DIRS:
            continue
        tech = _detect_tech(item, max_depth)
        if tech:
            modules.append((item.name, tech))
    return modules


def _detect_tech(path: Path, depth: int) -> str:
    """Detect technology in a directory."""
    for marker, tech in _MODULE_MARKERS.items():
        if (path / marker).exists():
            return tech
    if (path / "src").is_dir():
        return "source directory"
    if depth > 1:
        for sub in path.iterdir():
            if sub.is_dir() and not sub.name.startswith("."):
                result = _detect_tech(sub, depth - 1)
                if result:
                    return result
    return ""


def _generate_config(project_name: str, modules: list[tuple[str, str]]) -> str:
    """Generate orchestrator.yaml content."""
    lines = [
        f"# {CONFIG_FILENAME} — lindy-orchestrator configuration",
        "",
        "project:",
        f'  name: "{project_name}"',
        '  branch_prefix: "af"',
        "",
        "modules:",
    ]
    for name, tech in modules:
        lines.append(f"  - name: {name}")
        lines.append(f"    path: {name}/")
        lines.append(f"    # tech: {tech}")
        lines.append(f"    # repo: yourorg/{project_name}-{name}")
        lines.append("    # ci_workflow: ci.yml")
        lines.append("")

    lines.extend(
        [
            "planner:",
            "  mode: cli  # cli | api",
            "  # model: claude-sonnet-4-20250514  # for api mode",
            "",
            "dispatcher:",
            "  timeout_seconds: 1800",
            "  permission_mode: bypassPermissions",
            "",
            "# qa_gates:",
            "#   custom:",
            "#     - name: pytest",
            '#       command: "pytest --tb=short -q"',
            '#       cwd: "{module_path}"',
            "",
            "safety:",
            "  dry_run: false",
            "  max_retries_per_task: 2",
            "  max_parallel: 3",
        ]
    )
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# onboard
# ---------------------------------------------------------------------------


@app.command()
def onboard(
    depth: int = typer.Option(1, "--depth", help="Directory scan depth"),
    non_interactive: bool = typer.Option(
        False, "--non-interactive", "-y", help="Skip all questions, use defaults"
    ),
    force: bool = typer.Option(False, "--force", help="Overwrite existing files"),
):
    """Deep project onboarding: analyze, interview, generate artifacts.

    Scans the project, asks targeted questions about structure and conventions,
    then generates CLAUDE.md (root + per-module), CONTRACTS.md, STATUS.md,
    and orchestrator.yaml with full context.
    """
    from .discovery.analyzer import analyze_project
    from .discovery.generator import generate_artifacts
    from .discovery.interview import run_interview

    cwd = Path.cwd()
    console.print(f"[bold]lindy-orchestrate v{__version__}[/] — Project Onboarding\n")

    # Phase 1: Static analysis
    console.print("[bold cyan][1/3][/] Analyzing project structure...")
    profile = analyze_project(cwd, max_depth=depth)

    if not profile.modules:
        console.print("[yellow]No modules detected.[/] Use `init --modules` instead.")
        raise typer.Exit(1)

    if non_interactive:
        console.print(f"  [dim]Detected {len(profile.modules)} module(s):[/]")
        for mod in profile.modules:
            tech = ", ".join(mod.tech_stack) or "unknown"
            markers = (
                [
                    f.name
                    for f in Path(cwd / mod.path).iterdir()
                    if f.name in _MODULE_MARKERS and f.is_file()
                ]
                if (cwd / mod.path).is_dir()
                else []
            )
            marker_hint = f" (markers: {', '.join(markers)})" if markers else ""
            console.print(f"    [dim]• {mod.name} — {tech}{marker_hint}[/]")
        if profile.detected_ci:
            console.print(f"  [dim]CI detected: {profile.detected_ci}[/]")
        if profile.monorepo:
            console.print(f"  [dim]Structure: monorepo ({len(profile.modules)} modules)[/]")
        else:
            console.print("  [dim]Structure: single module[/]")

    # Phase 2: Interactive discovery
    console.print("[bold cyan][2/3][/] Project discovery...")
    context = run_interview(profile, non_interactive=non_interactive)

    # Phase 3: Generate artifacts
    console.print("[bold cyan][3/3][/] Generating artifacts...\n")
    written = generate_artifacts(context, output_dir=cwd, force=force)

    console.print(f"\n[bold green]Onboarding complete![/] {len(written)} files generated.")
    console.print("\nNext steps:")
    console.print("  1. Review generated CLAUDE.md files and refine conventions")
    if context.coordination_complexity >= 2:
        console.print("  2. Fill in CONTRACTS.md with specific interface definitions")
    console.print(
        f"  {'3' if context.coordination_complexity >= 2 else '2'}. "
        f'Run: lindy-orchestrate plan "Your goal here"'
    )


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------


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
):
    """Execute a goal with full orchestration.

    Goal can be provided as argument, from a file (--file goal.md), or stdin (--file -).
    Use --plan to execute a previously saved plan JSON directly (skips LLM planning).
    """
    from .scheduler import execute_plan

    cfg = _load_cfg(config)
    if dry_run:
        cfg.safety.dry_run = True

    # Verify claude CLI exists
    if not find_claude_cli():
        console.print("[red]Error: Claude CLI not found in PATH.[/]")
        console.print("Install: https://docs.anthropic.com/en/docs/claude-code")
        raise typer.Exit(1)

    # Load plan from file or generate from goal
    if plan_file:
        plan_path = Path(plan_file)
        if not plan_path.exists():
            console.print(f"[red]Plan file not found: {plan_file}[/]")
            raise typer.Exit(1)
        plan_data = json.loads(plan_path.read_text(encoding="utf-8"))
        plan = _plan_from_dict(plan_data)
        goal = plan.goal
        console.print(f"[bold]lindy-orchestrate v{__version__}[/]")
        console.print(f"Goal: [bold]{goal}[/]")
        console.print(f"[green]Loaded plan from {plan_file}[/]\n")
    else:
        from .planner import generate_plan

        goal = _resolve_goal(goal, file)

        console.print(f"[bold]lindy-orchestrate v{__version__}[/]")
        console.print(f"Goal: [bold]{goal}[/]")

    logger = ActionLogger(cfg.log_path)
    sessions = SessionManager(cfg.sessions_path)
    session = sessions.create(goal=goal)
    console.print(f"Session: {session.session_id}\n")

    start = time.monotonic()

    def on_progress(msg: str):
        console.print(msg)

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
    session.plan_json = _plan_to_dict(plan)
    sessions.save(session)

    # Auto-persist plan to .orchestrator/plans/
    _persist_plan(cfg.root, plan)

    console.print(f"\n  [bold]{len(plan.tasks)} tasks planned:[/]")
    for t in plan.tasks:
        deps = f" [dim](depends on: {t.depends_on})[/]" if t.depends_on else ""
        console.print(f"    {t.id}. [bold][{t.module}][/] {t.description}{deps}")

    # Step 3: Execute
    console.print("\n[bold cyan][2/3][/] Executing tasks...")
    plan = execute_plan(plan, cfg, logger, on_progress=on_progress, verbose=verbose)

    # Step 4: Report
    console.print("\n[bold cyan][3/3][/] Generating report...")
    duration = round(time.monotonic() - start, 1)

    completed = [t for t in plan.tasks if t.status.value == "completed"]
    failed = [t for t in plan.tasks if t.status.value == "failed"]

    if failed:
        report = f"GOAL PAUSED: {goal}\n\nCompleted: {len(completed)}/{len(plan.tasks)}\n"
        for t in failed:
            report += f"Failed: Task {t.id} [{t.module}] {t.description}\n"
    else:
        report = f"GOAL COMPLETED: {goal}\n\n"
        for t in completed:
            report += f"- [{t.module}] {t.description}\n"

    print_goal_report(report, dispatches=len(plan.tasks), duration=duration)

    # Update session with final plan state
    session.plan_json = _plan_to_dict(plan)
    session.completed_tasks = [
        {"id": t.id, "module": t.module, "description": t.description} for t in completed
    ]
    if failed:
        session.status = "paused"
        sessions.save(session)
    else:
        sessions.complete(session)

    logger.log_action(
        "session_end",
        details={
            "duration_seconds": duration,
            "completed": len(completed),
            "failed": len(failed),
        },
    )


# ---------------------------------------------------------------------------
# plan (dry plan only)
# ---------------------------------------------------------------------------


@app.command()
def plan(
    goal: Optional[str] = typer.Argument(None, help="Natural language goal to decompose"),
    file: Optional[str] = typer.Option(
        None, "-f", "--file", help="Read goal from file (use '-' for stdin)"
    ),
    config: Optional[str] = typer.Option(None, "-c", "--config"),
    output_file: Optional[str] = typer.Option(None, "-o", "--output", help="Save plan as JSON"),
):
    """Generate a task plan without executing it.

    Goal can be provided as argument, from a file (--file goal.md), or stdin (--file -).
    """
    from .planner import generate_plan

    goal = _resolve_goal(goal, file)
    cfg = _load_cfg(config)

    console.print(f"[bold]lindy-orchestrate v{__version__}[/]")
    console.print(f"Goal: [bold]{goal}[/]\n")

    progress = PlanProgress(console=console)

    def on_progress(msg: str):
        console.print(msg)

    progress.start()
    try:
        plan_result = generate_plan(goal, cfg, on_progress=on_progress, progress=progress)
    finally:
        if progress._live is not None:
            progress.stop()

    console.print(f"\n[bold]{len(plan_result.tasks)} tasks:[/]\n")
    for t in plan_result.tasks:
        deps = f" (depends on: {t.depends_on})" if t.depends_on else ""
        qa = ", ".join(q.gate for q in t.qa_checks) if t.qa_checks else "none"
        console.print(f"  {t.id}. [{t.module}] {t.description}{deps}")
        console.print(f"     QA: {qa}")
        if t.prompt:
            console.print(f"     Prompt: {t.prompt[:100]}...")

    # Auto-persist plan
    plan_json_path = _persist_plan(cfg.root, plan_result)
    console.print(f"\n[green]Plan saved to {plan_json_path}[/]")
    console.print(f"[dim]To execute: lindy-orchestrate run --plan {plan_json_path}[/]")

    if output_file:
        data = _plan_to_dict(plan_result)
        Path(output_file).write_text(json.dumps(data, indent=2, default=str))
        console.print(f"[green]Also saved to {output_file}[/]")


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


@app.command()
def status(
    config: Optional[str] = typer.Option(None, "-c", "--config"),
    as_json: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Show all module statuses (no LLM calls)."""
    cfg = _load_cfg(config)

    modules_data = []
    for mod in cfg.modules:
        path = cfg.status_path(mod.name)
        if path.exists():
            s = parse_status_md(path)
            open_reqs = [r for r in s.requests if r.status.upper() == "OPEN"]
            modules_data.append(
                {
                    "name": mod.name,
                    "health": s.meta.overall_health,
                    "last_updated": s.meta.last_updated,
                    "active_count": len(s.active_work),
                    "open_requests": len(open_reqs),
                    "blocker_count": len(s.blockers),
                    "blockers": s.blockers,
                }
            )
        else:
            modules_data.append(
                {
                    "name": mod.name,
                    "health": "?",
                    "last_updated": "N/A",
                    "active_count": 0,
                    "open_requests": 0,
                    "blocker_count": 0,
                }
            )

    if as_json:
        console.print_json(json.dumps(modules_data, indent=2))
    else:
        print_status_table(modules_data)


# ---------------------------------------------------------------------------
# logs
# ---------------------------------------------------------------------------


@app.command()
def logs(
    last: int = typer.Option(20, "-n", "--last", help="Show last N entries"),
    config: Optional[str] = typer.Option(None, "-c", "--config"),
    as_json: bool = typer.Option(False, "--json", help="Output raw JSONL"),
):
    """Show recent action logs."""
    cfg = _load_cfg(config)
    log_path = cfg.log_path

    if not log_path.exists():
        console.print("[dim]No logs found.[/]")
        return

    lines = log_path.read_text(encoding="utf-8").strip().splitlines()
    recent = lines[-last:]

    if as_json:
        for line in recent:
            console.print(line)
        return

    for line in recent:
        try:
            entry = json.loads(line)
            ts = entry.get("timestamp", "")[:19]
            action = entry.get("action", "?")
            result = entry.get("result", "?")

            color = {"success": "green", "error": "red", "fail": "red", "pass": "green"}.get(
                result, "yellow"
            )
            console.print(f"  [{color}]{result:>7}[/] {ts} {action}")

            details = entry.get("details", {})
            if details:
                for k, v in list(details.items())[:3]:
                    console.print(f"          {k}: {v}")
        except json.JSONDecodeError:
            console.print(f"  [dim]{line[:100]}[/]")


# ---------------------------------------------------------------------------
# resume
# ---------------------------------------------------------------------------


@app.command()
def resume(
    session_id: Optional[str] = typer.Argument(None, help="Session ID to resume"),
    config: Optional[str] = typer.Option(None, "-c", "--config"),
    verbose: bool = typer.Option(False, "-v", "--verbose", help="Show detailed output"),
):
    """Resume a previous session from its last checkpoint.

    Skips already-completed tasks and re-executes failed/pending ones.
    """
    from .scheduler import execute_plan

    cfg = _load_cfg(config)
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
    console.print(f"Goal: {session.goal}")
    console.print(f"Status: {session.status}")

    if session.status == "completed":
        console.print("[yellow]Session already completed. Nothing to resume.[/]")
        return

    if not session.plan_json:
        console.print("[yellow]No saved plan found. Re-running from scratch...[/]")
        run(goal=session.goal, config=config, dry_run=False, verbose=verbose)
        return

    # Restore plan from checkpoint
    plan = _plan_from_dict(session.plan_json)
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
    start = time.monotonic()

    def on_progress(msg: str):
        console.print(msg)

    console.print("\n[bold cyan]Resuming execution...[/]")
    plan = execute_plan(plan, cfg, logger, on_progress=on_progress, verbose=verbose)

    duration = round(time.monotonic() - start, 1)
    completed = [t for t in plan.tasks if t.status.value == "completed"]
    failed = [t for t in plan.tasks if t.status.value == "failed"]

    print_goal_report(
        f"{'GOAL COMPLETED' if not failed else 'GOAL PAUSED'}: {session.goal}\n\n"
        f"Completed: {len(completed)}/{len(plan.tasks)} tasks",
        dispatches=len(plan.tasks),
        duration=duration,
    )

    # Update session
    session.plan_json = _plan_to_dict(plan)
    session.completed_tasks = [
        {"id": t.id, "module": t.module, "description": t.description} for t in completed
    ]
    if failed:
        session.status = "paused"
        sessions.save(session)
    else:
        sessions.complete(session)


# ---------------------------------------------------------------------------
# gc (garbage collection)
# ---------------------------------------------------------------------------


@app.command()
def gc(
    config: Optional[str] = typer.Option(None, "-c", "--config"),
    apply: bool = typer.Option(
        False, "--apply", help="Actually perform cleanup (default: dry run)"
    ),
    branch_age: int = typer.Option(14, "--branch-age", help="Max age for task branches (days)"),
    session_age: int = typer.Option(30, "--session-age", help="Max age for sessions (days)"),
    log_size: int = typer.Option(10, "--log-size", help="Max log file size (MB)"),
    status_stale: int = typer.Option(7, "--status-stale", help="STATUS.md stale threshold (days)"),
):
    """Clean up stale branches, old sessions, oversized logs, and orphan plans.

    Runs in dry-run mode by default. Use --apply to execute cleanup.
    """
    from .gc import format_gc_report, run_gc

    cfg = _load_cfg(config)
    mode = "[red]APPLY[/]" if apply else "[yellow]DRY RUN[/]"
    console.print(f"[bold]lindy-orchestrate gc[/] — {mode}\n")

    report = run_gc(
        cfg,
        apply=apply,
        max_branch_age_days=branch_age,
        max_session_age_days=session_age,
        max_log_size_mb=log_size,
        status_stale_days=status_stale,
    )

    console.print(format_gc_report(report))

    if not report.actions:
        console.print("[bold green]Workspace is clean.[/]")
    elif not apply:
        console.print(
            f"\n[yellow]{report.action_count} action(s) found.[/] Run with --apply to execute."
        )


# ---------------------------------------------------------------------------
# scan
# ---------------------------------------------------------------------------


@app.command()
def scan(
    config: Optional[str] = typer.Option(None, "-c", "--config"),
    module: Optional[str] = typer.Option(None, "--module", help="Scan specific module"),
    grade_only: bool = typer.Option(False, "--grade-only", help="Only show grades"),
):
    """Scan for entropy: architecture drift, contract violations, quality decay."""
    from .entropy.scanner import format_scan_report, run_scan

    cfg = _load_cfg(config)

    console.print("[bold]lindy-orchestrate scan[/] — Entropy Scanner\n")

    report = run_scan(cfg, module_filter=module)

    output = format_scan_report(report, grade_only=grade_only)
    console.print(output)

    error_count = len([f for f in report.findings if f.severity == "error"])
    warning_count = len([f for f in report.findings if f.severity == "warning"])

    if error_count:
        console.print(f"\n[red]{error_count} error(s), {warning_count} warning(s)[/]")
    elif warning_count:
        console.print(f"\n[yellow]{warning_count} warning(s)[/]")
    else:
        console.print("\n[bold green]No issues found.[/]")


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------


@app.command()
def validate(
    config: Optional[str] = typer.Option(None, "-c", "--config"),
):
    """Validate config and STATUS.md files."""
    try:
        cfg = _load_cfg(config)
    except Exception as e:
        console.print(f"[red]Config error: {e}[/]")
        raise typer.Exit(1)

    console.print(f"[green]Config valid[/]: {len(cfg.modules)} modules")

    errors = 0
    for mod in cfg.modules:
        mod_path = cfg.module_path(mod.name)
        if not mod_path.exists():
            console.print(f"  [red]Module path missing: {mod_path}[/]")
            errors += 1
        else:
            console.print(f"  [green]{mod.name}[/]: {mod_path}")

        status_path = cfg.status_path(mod.name)
        if not status_path.exists():
            console.print(f"    [yellow]STATUS.md missing: {status_path}[/]")
        else:
            s = parse_status_md(status_path)
            console.print(
                f"    STATUS.md: health={s.meta.overall_health}, "
                f"active={len(s.active_work)}, blockers={len(s.blockers)}"
            )

    # Check claude CLI
    if find_claude_cli():
        console.print("[green]Claude CLI found[/]")
    else:
        console.print("[yellow]Claude CLI not found in PATH[/]")

    if errors:
        console.print(f"\n[red]{errors} error(s) found.[/]")
        raise typer.Exit(1)
    else:
        console.print("\n[bold green]All checks passed.[/]")


# ---------------------------------------------------------------------------
# issues
# ---------------------------------------------------------------------------


@app.command()
def issues(
    config: Optional[str] = typer.Option(None, "-c", "--config"),
    label: Optional[str] = typer.Option(None, "--label", help="Filter by label"),
    status: str = typer.Option("open", "--status", help="Issue status filter"),
    limit: int = typer.Option(20, "--limit", "-n", help="Max issues to fetch"),
    as_json: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """List issues from the configured tracker."""
    from .trackers import create_tracker

    cfg = _load_cfg(config)

    if not cfg.tracker.enabled:
        console.print("[yellow]Tracker is disabled.[/] Set tracker.enabled: true in config.")
        return

    tracker = create_tracker(cfg.tracker.provider, repo=cfg.tracker.repo)
    labels = [label] if label else (cfg.tracker.labels or None)

    try:
        issue_list = tracker.fetch_issues(
            project=cfg.project.name,
            labels=labels,
            status=status,
            limit=limit,
        )
    except Exception as e:
        console.print(f"[red]Failed to fetch issues: {e}[/]")
        raise typer.Exit(1)

    if as_json:
        from dataclasses import asdict

        console.print_json(json.dumps([asdict(i) for i in issue_list], indent=2))
        return

    if not issue_list:
        console.print("[dim]No issues found.[/]")
        return

    console.print(f"[bold]{len(issue_list)} issue(s):[/]\n")
    for issue in issue_list:
        labels_str = f" [{', '.join(issue.labels)}]" if issue.labels else ""
        console.print(f"  [bold]#{issue.id}[/]{labels_str} {issue.title}")
        if issue.url:
            console.print(f"    [dim]{issue.url}[/]")


@app.command(name="run-issue")
def run_issue(
    issue_id: str = typer.Argument(..., help="Issue ID to execute"),
    config: Optional[str] = typer.Option(None, "-c", "--config"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Plan only, don't execute"),
    verbose: bool = typer.Option(False, "-v", "--verbose"),
):
    """Fetch an issue from the tracker and execute it as a goal.

    Fetches the issue, uses its title + body as the goal, generates a plan,
    executes it, and syncs status back to the tracker.
    """
    from .planner import generate_plan
    from .scheduler import execute_plan
    from .trackers import create_tracker

    cfg = _load_cfg(config)

    if not cfg.tracker.enabled:
        console.print("[yellow]Tracker is disabled.[/] Set tracker.enabled: true in config.")
        raise typer.Exit(1)

    tracker = create_tracker(cfg.tracker.provider, repo=cfg.tracker.repo)

    # Fetch the specific issue
    try:
        all_issues = tracker.fetch_issues(
            project=cfg.project.name,
            status="open",
            limit=100,
        )
    except Exception as e:
        console.print(f"[red]Failed to fetch issues: {e}[/]")
        raise typer.Exit(1)

    issue = next((i for i in all_issues if i.id == issue_id), None)
    if not issue:
        console.print(f"[red]Issue #{issue_id} not found or not open.[/]")
        raise typer.Exit(1)

    goal = f"{issue.title}\n\n{issue.body}" if issue.body else issue.title
    console.print(f"[bold]Issue #{issue.id}:[/] {issue.title}")
    console.print(f"[dim]{issue.url}[/]\n")

    if dry_run:
        cfg.safety.dry_run = True

    # Verify claude CLI
    if not find_claude_cli():
        console.print("[red]Error: Claude CLI not found in PATH.[/]")
        raise typer.Exit(1)

    logger = ActionLogger(cfg.log_path)
    sessions = SessionManager(cfg.sessions_path)
    session = sessions.create(goal=f"[Issue #{issue.id}] {issue.title}")

    console.print(f"Session: {session.session_id}\n")

    start = time.monotonic()

    def on_progress(msg: str):
        console.print(msg)

    # Plan
    logger.log_action(
        "session_start",
        details={"goal": goal, "issue_id": issue.id, "dry_run": cfg.safety.dry_run},
    )
    console.print("[bold cyan][1/3][/] Generating task plan from issue...")
    progress = PlanProgress(console=console)
    progress.start()

    try:
        plan_result = generate_plan(goal, cfg, on_progress=on_progress, progress=progress)
    except Exception as e:
        progress.stop(f"Planning failed: {e}")
        console.print(f"[red]Planning failed: {e}[/]")
        session.status = "failed"
        sessions.save(session)
        raise typer.Exit(1)
    finally:
        if progress._live is not None:
            progress.stop()

    session.plan_json = _plan_to_dict(plan_result)
    sessions.save(session)
    _persist_plan(cfg.root, plan_result)

    console.print(f"\n  [bold]{len(plan_result.tasks)} tasks planned:[/]")
    for t in plan_result.tasks:
        deps = f" [dim](depends on: {t.depends_on})[/]" if t.depends_on else ""
        console.print(f"    {t.id}. [bold][{t.module}][/] {t.description}{deps}")

    # Execute
    console.print("\n[bold cyan][2/3][/] Executing tasks...")
    plan_result = execute_plan(plan_result, cfg, logger, on_progress=on_progress, verbose=verbose)

    # Report
    console.print("\n[bold cyan][3/3][/] Generating report...")
    duration = round(time.monotonic() - start, 1)

    completed = [t for t in plan_result.tasks if t.status.value == "completed"]
    failed = [t for t in plan_result.tasks if t.status.value == "failed"]

    print_goal_report(
        f"{'GOAL COMPLETED' if not failed else 'GOAL PAUSED'}: {issue.title}\n\n"
        f"Completed: {len(completed)}/{len(plan_result.tasks)} tasks",
        dispatches=len(plan_result.tasks),
        duration=duration,
    )

    # Sync back to tracker
    if cfg.tracker.sync_on_complete and not cfg.safety.dry_run:
        task_summary = "\n".join(
            f"- [{t.module}] {t.description}: {t.status.value}" for t in plan_result.tasks
        )
        comment = (
            f"**lindy-orchestrator** completed execution.\n\n"
            f"**Result:** {'All tasks completed' if not failed else f'{len(failed)} task(s) failed'}\n"
            f"**Duration:** {duration}s\n\n"
            f"### Tasks\n{task_summary}"
        )
        try:
            tracker.add_comment(issue.id, comment)
            if not failed:
                tracker.update_status(issue.id, "closed")
                console.print(f"[green]Issue #{issue.id} closed with summary.[/]")
            else:
                console.print(f"[yellow]Comment added to issue #{issue.id}.[/]")
        except Exception as e:
            console.print(f"[yellow]Failed to sync to tracker: {e}[/]")

    # Update session
    session.plan_json = _plan_to_dict(plan_result)
    session.completed_tasks = [
        {"id": t.id, "module": t.module, "description": t.description} for t in completed
    ]
    if failed:
        session.status = "paused"
        sessions.save(session)
    else:
        sessions.complete(session)

    logger.log_action(
        "session_end",
        details={
            "duration_seconds": duration,
            "completed": len(completed),
            "failed": len(failed),
            "issue_id": issue.id,
        },
    )


# ---------------------------------------------------------------------------
# mailbox
# ---------------------------------------------------------------------------


@app.command()
def mailbox(
    module: Optional[str] = typer.Argument(None, help="Module name to view messages for"),
    send_to: Optional[str] = typer.Option(None, "--send-to", help="Send a message to a module"),
    send_from: Optional[str] = typer.Option(None, "--send-from", help="Sender module name"),
    message: Optional[str] = typer.Option(None, "--message", "-m", help="Message content"),
    priority: str = typer.Option("normal", "--priority", "-p", help="Message priority"),
    config: Optional[str] = typer.Option(None, "-c", "--config"),
    as_json: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """View or send inter-agent mailbox messages.

    Examples:
      lindy-orchestrate mailbox frontend          # View pending messages for frontend
      lindy-orchestrate mailbox --send-to backend --send-from frontend -m "Need API endpoint"
    """
    from .mailbox import Mailbox, Message

    cfg = _load_cfg(config)

    if not cfg.mailbox.enabled:
        console.print("[yellow]Mailbox is disabled.[/] Set mailbox.enabled: true in config.")
        return

    mb = Mailbox(cfg.root / cfg.mailbox.dir)

    # Send mode
    if send_to and message:
        from_mod = send_from or "cli"
        msg = Message(
            from_module=from_mod,
            to_module=send_to,
            content=message,
            priority=priority,
        )
        mb.send(msg)
        console.print(
            f"[green]Sent[/] message to [bold]{send_to}[/] from [bold]{from_mod}[/]: {message}"
        )
        return

    if send_to and not message:
        console.print("[red]--message is required when using --send-to[/]")
        raise typer.Exit(1)

    # View mode
    if not module:
        # Show summary for all modules
        console.print("[bold]Mailbox Summary[/]\n")
        for mod in cfg.modules:
            count = mb.pending_count(mod.name)
            if count > 0:
                console.print(f"  [bold]{mod.name}[/]: {count} pending message(s)")
            else:
                console.print(f"  [dim]{mod.name}[/]: no pending messages")
        return

    # Show messages for specific module
    messages = mb.receive(module, unread_only=True)
    if as_json:
        import json as _json
        from dataclasses import asdict

        console.print_json(_json.dumps([asdict(m) for m in messages], indent=2, default=str))
        return

    if not messages:
        console.print(f"[dim]No pending messages for {module}.[/]")
        return

    console.print(f"[bold]Pending messages for {module}[/] ({len(messages)}):\n")
    for msg in messages:
        priority_tag = f" [{msg.priority.upper()}]" if msg.priority != "normal" else ""
        console.print(
            f"  [bold]{msg.from_module}[/]{priority_tag} ({msg.message_type}): {msg.content}"
        )
        console.print(f"    [dim]id={msg.id} at {msg.created_at}[/]")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_goal(goal: str | None, file: str | None) -> str:
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


def _load_cfg(config_path: str | None):
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


def _plan_to_dict(plan: TaskPlan) -> dict:
    """Serialize a TaskPlan to a JSON-safe dict."""
    from .models import plan_to_dict

    return plan_to_dict(plan)


def _plan_from_dict(data: dict) -> TaskPlan:
    """Deserialize a TaskPlan from a dict."""
    from .models import plan_from_dict

    return plan_from_dict(data)


def _persist_plan(root: Path, plan: TaskPlan) -> Path:
    """Auto-save plan to .orchestrator/plans/. Returns the JSON path."""
    import re
    from datetime import datetime, timezone

    plans_dir = root / ".orchestrator" / "plans"
    plans_dir.mkdir(parents=True, exist_ok=True)

    # Generate slug from goal
    slug = re.sub(r"[^a-z0-9]+", "-", plan.goal.lower().strip())[:50].strip("-")
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    filename = f"{ts}-{slug}" if slug else ts

    # Save JSON
    json_path = plans_dir / f"{filename}.json"
    json_path.write_text(json.dumps(_plan_to_dict(plan), indent=2, default=str))

    # Save human-readable latest.md
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
