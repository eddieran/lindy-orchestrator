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
from .reporter import print_goal_report, print_status_table
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


def _detect_modules(root: Path, max_depth: int) -> list[tuple[str, str]]:
    """Auto-detect project modules by scanning for marker files."""
    modules = []
    for item in sorted(root.iterdir()):
        if not item.is_dir() or item.name.startswith("."):
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
    goal: str = typer.Argument(..., help="Natural language goal to achieve"),
    config: Optional[str] = typer.Option(None, "-c", "--config", help="Config YAML path"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Read and analyze only"),
    verbose: bool = typer.Option(False, "-v", "--verbose", help="Show detailed output"),
):
    """Execute a goal with full orchestration."""
    from .planner import generate_plan
    from .scheduler import execute_plan

    cfg = _load_cfg(config)
    if dry_run:
        cfg.safety.dry_run = True

    # Verify claude CLI exists
    if not find_claude_cli():
        console.print("[red]Error: Claude CLI not found in PATH.[/]")
        console.print("Install: https://docs.anthropic.com/en/docs/claude-code")
        raise typer.Exit(1)

    logger = ActionLogger(cfg.log_path)
    sessions = SessionManager(cfg.sessions_path)
    session = sessions.create(goal=goal)

    console.print(f"[bold]lindy-orchestrate v{__version__}[/]")
    console.print(f"Goal: [bold]{goal}[/]")
    console.print(f"Session: {session.session_id}\n")

    start = time.monotonic()

    def on_progress(msg: str):
        console.print(msg)

    # Step 1-2: Plan
    logger.log_action("session_start", details={"goal": goal, "dry_run": cfg.safety.dry_run})
    console.print("[bold cyan][1/3][/] Generating task plan...")

    try:
        plan = generate_plan(goal, cfg, on_progress=on_progress)
    except Exception as e:
        console.print(f"[red]Planning failed: {e}[/]")
        session.status = "failed"
        sessions.save(session)
        raise typer.Exit(1)

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

    # Update session
    session.completed_tasks = [
        {"id": t.id, "module": t.module, "description": t.description} for t in completed
    ]
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
    goal: str = typer.Argument(..., help="Natural language goal to decompose"),
    config: Optional[str] = typer.Option(None, "-c", "--config"),
    output_file: Optional[str] = typer.Option(None, "-o", "--output", help="Save plan as JSON"),
):
    """Generate a task plan without executing it."""
    from .planner import generate_plan

    cfg = _load_cfg(config)

    console.print(f"[bold]lindy-orchestrate v{__version__}[/]")
    console.print(f"Goal: [bold]{goal}[/]\n")

    def on_progress(msg: str):
        console.print(msg)

    plan_result = generate_plan(goal, cfg, on_progress=on_progress)

    console.print(f"\n[bold]{len(plan_result.tasks)} tasks:[/]\n")
    for t in plan_result.tasks:
        deps = f" (depends on: {t.depends_on})" if t.depends_on else ""
        qa = ", ".join(q.gate for q in t.qa_checks) if t.qa_checks else "none"
        console.print(f"  {t.id}. [{t.module}] {t.description}{deps}")
        console.print(f"     QA: {qa}")
        if t.prompt:
            console.print(f"     Prompt: {t.prompt[:100]}...")

    if output_file:
        import dataclasses

        data = {
            "goal": plan_result.goal,
            "tasks": [dataclasses.asdict(t) for t in plan_result.tasks],
        }
        Path(output_file).write_text(json.dumps(data, indent=2, default=str))
        console.print(f"\n[green]Plan saved to {output_file}[/]")


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
):
    """Resume a previous session."""
    cfg = _load_cfg(config)
    sessions = SessionManager(cfg.sessions_path)

    if session_id:
        session = sessions.load(session_id)
    else:
        session = sessions.load_latest()

    if not session:
        console.print("[red]No session found to resume.[/]")
        raise typer.Exit(1)

    console.print(f"Resuming session [bold]{session.session_id}[/]")
    console.print(f"Goal: {session.goal}")
    console.print(f"Status: {session.status}")

    if session.status == "completed":
        console.print("[yellow]Session already completed.[/]")
        return

    # Re-run with the same goal
    run(goal=session.goal, config=config, dry_run=False, verbose=False)


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
# Helpers
# ---------------------------------------------------------------------------


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
