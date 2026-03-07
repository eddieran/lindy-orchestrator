"""Init and onboarding commands for lindy-orchestrate."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer

from . import __version__
from .config import CONFIG_FILENAME
from .status.templates import generate_status_md

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


def register_init_commands(app: typer.Typer, console) -> None:
    """Register init and onboard commands on the Typer app."""

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
            console.print(
                f"\n[yellow]{CONFIG_FILENAME} already exists.[/] Use --force to overwrite."
            )
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
