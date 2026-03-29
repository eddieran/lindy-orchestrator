"""Unified onboard command — merges init, scaffold, and onboard into one entry point.

Detects project state and adapts behavior:
- Empty project (no source files, no config) → scaffold mode (LLM-driven)
- Existing project without orchestrator.yaml → init+onboard mode (detect, interview, generate)
- Already onboarded (has orchestrator.yaml) → re-onboard/optimize mode
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

from . import __version__
from .cli_helpers import resolve_goal, validate_provider
from .config import CONFIG_FILENAME, DispatcherConfig
from .models import CrossModuleDep, DiscoveryContext, ModuleProfile
from .providers import create_provider


# ---------------------------------------------------------------------------
# Constants (preserved from cli_init.py)
# ---------------------------------------------------------------------------

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

# ---------------------------------------------------------------------------
# LLM scaffold prompt (preserved from cli_scaffold.py)
# ---------------------------------------------------------------------------

SCAFFOLD_SYSTEM_PROMPT = """\
You are a project architect. Analyze the following project description and \
produce a JSON structure that defines the project's orchestration scaffold.

You must return ONLY a valid JSON object (no markdown fencing, no explanation) \
with this exact structure:

{
  "project_name": "short-slug-name",
  "project_description": "One-line summary",
  "modules": [
    {
      "name": "module-name",
      "path": "module-name",
      "tech_stack": ["Python", "FastAPI"],
      "detected_patterns": ["REST API", "database ORM"],
      "test_commands": ["pytest"],
      "build_commands": ["pip install -e ."],
      "lint_commands": ["ruff check ."]
    }
  ],
  "cross_deps": [
    {
      "from_module": "frontend",
      "to_module": "backend",
      "interface_type": "api",
      "description": "REST API calls"
    }
  ],
  "coordination_complexity": 2,
  "branch_prefix": "af",
  "sensitive_paths": [".env", "*.key"],
  "qa_requirements": {
    "module-name": ["pytest", "ruff check ."]
  },
  "monorepo": true
}

Rules:
- "modules": At least one module. Each has name, path, tech_stack, and commands.
- "path": Relative directory path for the module (e.g., "backend", "frontend", ".").
  For a single-module project, use the module name as the path.
- "tech_stack": List of technologies (languages, frameworks).
- "detected_patterns": Architectural patterns (e.g., "REST API", "frontend SPA", \
"database ORM", "async job queue", "containerized", "microservices").
- "cross_deps": Dependencies between modules. Only include if there are 2+ modules.
  "interface_type" is one of: "api", "file", "database", "env_var", "message_queue".
- "coordination_complexity": 1 (single module or loosely coupled), \
2 (moderate cross-module deps), 3 (tight integration, shared state).
- "qa_requirements": Map of module name to list of QA commands.
- "sensitive_paths": Glob patterns for files that should never be committed.
- "monorepo": true if multiple modules in one repo, false for single module.
- Infer reasonable defaults for commands based on the tech stack.
"""


# ---------------------------------------------------------------------------
# Project state detection
# ---------------------------------------------------------------------------


def _has_config(cwd: Path) -> bool:
    """Check if config exists (new .orchestrator/config.yaml or legacy orchestrator.yaml)."""
    return (cwd / ".orchestrator" / "config.yaml").exists() or (cwd / CONFIG_FILENAME).exists()


def _has_source_files(cwd: Path) -> bool:
    """Check if the directory has any meaningful source files/modules."""
    for item in cwd.iterdir():
        if item.name.startswith(".") or item.name in _IGNORED_DIRS:
            continue
        if item.is_dir():
            tech = _detect_tech(item, 1)
            if tech:
                return True
        elif item.is_file():
            for marker in _MODULE_MARKERS:
                if item.name == marker:
                    return True
    return False


# ---------------------------------------------------------------------------
# Helpers preserved from cli_init.py
# ---------------------------------------------------------------------------


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
            '#       command: "pytest -x -q --tb=short"',
            '#       cwd: "{module_path}"',
            "#     - name: lint",
            '#       command: "ruff check ."',
            '#       cwd: "{module_path}"',
            "#       diff_only: true  # only lint changed files",
            "",
            "safety:",
            "  dry_run: false",
            "  max_retries_per_task: 2",
            "  max_parallel: 3",
            "",
            "mailbox:",
            "  enabled: true",
        ]
    )
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Helpers preserved from cli_scaffold.py
# ---------------------------------------------------------------------------


def _build_scaffold_prompt(description: str) -> str:
    """Build the full prompt for the scaffold LLM call."""
    return f"""{SCAFFOLD_SYSTEM_PROMPT}

## Project Description

{description}

Return ONLY the JSON object."""


def parse_scaffold_response(output: str) -> dict:
    """Parse the LLM JSON response into a dict.

    Handles markdown code fences and extracts the JSON object.
    """
    cleaned = re.sub(r"```(?:json)?\s*", "", output)
    cleaned = re.sub(r"```\s*$", "", cleaned)

    json_match = re.search(r"\{[\s\S]*\}", cleaned)
    if json_match:
        return json.loads(json_match.group())
    return json.loads(cleaned)


def scaffold_response_to_context(data: dict, output_dir: str = ".") -> DiscoveryContext:
    """Convert parsed LLM scaffold response into a DiscoveryContext."""
    modules = []
    for m in data.get("modules", []):
        modules.append(
            ModuleProfile(
                name=m["name"],
                path=m.get("path", m["name"]),
                tech_stack=m.get("tech_stack", []),
                detected_patterns=m.get("detected_patterns", []),
                test_commands=m.get("test_commands", []),
                build_commands=m.get("build_commands", []),
                lint_commands=m.get("lint_commands", []),
            )
        )

    cross_deps = []
    for d in data.get("cross_deps", []):
        cross_deps.append(
            CrossModuleDep(
                from_module=d["from_module"],
                to_module=d["to_module"],
                interface_type=d.get("interface_type", ""),
                description=d.get("description", ""),
            )
        )

    return DiscoveryContext(
        project_name=data.get("project_name", "project"),
        project_description=data.get("project_description", ""),
        root=output_dir,
        modules=modules,
        cross_deps=cross_deps,
        coordination_complexity=data.get("coordination_complexity", 1),
        branch_prefix=data.get("branch_prefix", "af"),
        sensitive_paths=data.get("sensitive_paths", [".env"]),
        qa_requirements=data.get("qa_requirements", {}),
        monorepo=data.get("monorepo", False),
    )


# ---------------------------------------------------------------------------
# Plan display
# ---------------------------------------------------------------------------


def _show_plan(
    console: Console, mode: str, cwd: Path, files_to_create: list[str], modules_info: list[str]
) -> None:
    """Display the interactive plan before executing."""
    mode_labels = {
        "scaffold": "Scaffold (LLM-driven, empty project)",
        "init_onboard": "Init + Onboard (detect modules, interview, generate artifacts)",
        "re_onboard": "Re-onboard (update existing configuration)",
    }
    console.print("\n[bold cyan]Onboarding Plan[/]")
    console.print(f"  Mode: [bold]{mode_labels.get(mode, mode)}[/]")
    console.print(f"  Directory: [bold]{cwd}[/]")

    if modules_info:
        console.print(f"\n  Modules detected ({len(modules_info)}):")
        for info in modules_info:
            console.print(f"    - {info}")

    if files_to_create:
        console.print(f"\n  Files to create/modify ({len(files_to_create)}):")
        for f in files_to_create:
            console.print(f"    - {f}")

    console.print()


# ---------------------------------------------------------------------------
# Mode executors
# ---------------------------------------------------------------------------


def _run_scaffold_mode(
    console: Console,
    cwd: Path,
    description: str | None,
    file: str | None,
    force: bool,
    non_interactive: bool,
    provider_name: str = "claude_cli",
) -> None:
    """Scaffold mode: LLM-driven generation for empty projects."""
    desc = resolve_goal(description, file)

    console.print(f"Description: [dim]{desc[:200]}{'...' if len(desc) > 200 else ''}[/]\n")

    # Step 1: LLM analysis
    console.print("[bold cyan][1/2][/] Analyzing project description with LLM...")
    prompt = _build_scaffold_prompt(desc)

    dispatcher_config = DispatcherConfig(
        timeout_seconds=120,
        permission_mode="bypassPermissions",
        provider=provider_name,
    )
    provider = create_provider(dispatcher_config)
    result = provider.dispatch_simple(
        module="scaffold",
        working_dir=cwd,
        prompt=prompt,
    )

    if not result.success:
        console.print(f"[red]LLM call failed: {result.output[:500]}[/]")
        raise typer.Exit(1)

    try:
        data = parse_scaffold_response(result.output)
    except (json.JSONDecodeError, ValueError) as e:
        console.print(f"[red]Failed to parse LLM response: {e}[/]")
        console.print(f"[dim]Raw output:\n{result.output[:1000]}[/]")
        raise typer.Exit(1)

    ctx = scaffold_response_to_context(data, output_dir=str(cwd))

    # Build plan info
    modules_info = []
    for mod in ctx.modules:
        tech = ", ".join(mod.tech_stack) or "unknown"
        modules_info.append(f"[bold]{mod.name}[/] ({tech})")

    files_to_create = [
        ".orchestrator/config.yaml",
        ".orchestrator/claude/root.md",
        ".orchestrator/architecture.md",
    ]
    for mod in ctx.modules:
        files_to_create.append(f".orchestrator/claude/{mod.name}.md")
        files_to_create.append(f".orchestrator/status/{mod.name}.md")
    if ctx.coordination_complexity >= 2:
        files_to_create.append(".orchestrator/contracts.md")
    files_to_create.extend(
        [
            ".orchestrator/docs/protocol.md",
            ".orchestrator/docs/conventions.md",
            ".orchestrator/docs/boundaries.md",
        ]
    )

    _show_plan(console, "scaffold", cwd, files_to_create, modules_info)

    if not non_interactive:
        if not typer.confirm("Proceed with this plan?", default=True):
            console.print("[yellow]Aborted.[/]")
            raise typer.Exit(0)

    # Step 2: Generate artifacts
    console.print("[bold cyan][2/2][/] Generating artifacts...\n")

    from .discovery.generator import generate_artifacts

    written = generate_artifacts(ctx, output_dir=cwd, force=force)

    console.print(f"\n[bold green]Onboarding complete![/] {len(written)} files generated.")
    console.print("\nNext steps:")
    console.print("  1. Review generated files and adjust conventions")
    console.print("  2. Create your module directories and start coding")
    console.print('  3. Run: lindy-orchestrate plan "Your first goal"')


def _run_init_onboard_mode(
    console: Console,
    cwd: Path,
    depth: int,
    force: bool,
    non_interactive: bool,
) -> None:
    """Init+Onboard mode: detect modules, interview, generate artifacts."""
    from .discovery.analyzer import analyze_project
    from .discovery.generator import generate_artifacts
    from .discovery.interview import run_interview

    # Phase 1: Static analysis
    console.print("[bold cyan][1/3][/] Analyzing project structure...")
    profile = analyze_project(cwd, max_depth=depth)

    if not profile.modules:
        console.print(
            "[yellow]No modules detected.[/] "
            "Provide a project description to use scaffold mode instead."
        )
        raise typer.Exit(1)

    # Build plan info
    modules_info = []
    for mod in profile.modules:
        tech = ", ".join(mod.tech_stack) or "unknown"
        modules_info.append(f"[bold]{mod.name}[/] ({tech})")

    files_to_create = [
        ".orchestrator/config.yaml",
        ".orchestrator/claude/root.md",
        ".orchestrator/architecture.md",
    ]
    for mod in profile.modules:
        files_to_create.append(f".orchestrator/claude/{mod.name}.md")
        files_to_create.append(f".orchestrator/status/{mod.name}.md")
    files_to_create.extend(
        [
            ".orchestrator/docs/protocol.md",
            ".orchestrator/docs/conventions.md",
            ".orchestrator/docs/boundaries.md",
            ".orchestrator/ (logs, sessions)",
            ".gitignore (update)",
        ]
    )

    _show_plan(console, "init_onboard", cwd, files_to_create, modules_info)

    if not non_interactive:
        if not typer.confirm("Proceed with this plan?", default=True):
            console.print("[yellow]Aborted.[/]")
            raise typer.Exit(0)

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


def _run_re_onboard_mode(
    console: Console,
    cwd: Path,
    depth: int,
    force: bool,
    non_interactive: bool,
) -> None:
    """Re-onboard mode: show current config, ask what to change."""
    from .config import load_config
    from .discovery.analyzer import analyze_project
    from .discovery.generator import generate_artifacts
    from .discovery.interview import run_interview

    # Load existing config (prefer new path, fall back to legacy)
    new_cfg_path = cwd / ".orchestrator" / "config.yaml"
    cfg = load_config(new_cfg_path if new_cfg_path.exists() else cwd / CONFIG_FILENAME)
    console.print(f"  Project: [bold]{cfg.project.name}[/]")
    console.print(f"  Modules: [bold]{len(cfg.modules)}[/]")
    for mod in cfg.modules:
        console.print(f"    - [bold]{mod.name}[/] ({mod.path})")

    # Re-analyze
    console.print("\n[bold cyan][1/3][/] Re-analyzing project structure...")
    profile = analyze_project(cwd, max_depth=depth)

    # Show differences
    existing_names = {m.name for m in cfg.modules}
    detected_names = {m.name for m in profile.modules}
    new_modules = detected_names - existing_names
    if new_modules:
        console.print(f"  [yellow]New modules detected: {', '.join(new_modules)}[/]")

    modules_info = []
    for mod in profile.modules:
        tech = ", ".join(mod.tech_stack) or "unknown"
        label = " [yellow](new)[/]" if mod.name in new_modules else ""
        modules_info.append(f"[bold]{mod.name}[/] ({tech}){label}")

    files_to_create = [
        ".orchestrator/config.yaml",
        ".orchestrator/claude/root.md",
        ".orchestrator/architecture.md",
    ]
    for mod in profile.modules:
        files_to_create.append(f".orchestrator/claude/{mod.name}.md")
        files_to_create.append(f".orchestrator/status/{mod.name}.md")
    files_to_create.extend(
        [
            ".orchestrator/docs/protocol.md",
            ".orchestrator/docs/conventions.md",
            ".orchestrator/docs/boundaries.md",
        ]
    )

    _show_plan(console, "re_onboard", cwd, files_to_create, modules_info)

    if not non_interactive:
        if not typer.confirm("Proceed with re-onboarding?", default=True):
            console.print("[yellow]Aborted.[/]")
            raise typer.Exit(0)

    # Phase 2: Interview
    console.print("[bold cyan][2/3][/] Project discovery...")
    context = run_interview(profile, non_interactive=non_interactive)

    # Phase 3: Regenerate
    console.print("[bold cyan][3/3][/] Regenerating artifacts...\n")
    written = generate_artifacts(context, output_dir=cwd, force=force)

    console.print(f"\n[bold green]Re-onboarding complete![/] {len(written)} files updated.")


# ---------------------------------------------------------------------------
# CLI registration
# ---------------------------------------------------------------------------


def register_onboard_command(app: typer.Typer, console: Console) -> None:
    """Register the unified onboard command on the Typer app."""

    @app.command()
    def onboard(
        description: Optional[str] = typer.Argument(
            None, help="Project description (triggers scaffold mode for empty projects)"
        ),
        file: Optional[str] = typer.Option(
            None, "-f", "--file", help="Read description from file (use '-' for stdin)"
        ),
        depth: int = typer.Option(1, "--depth", help="Directory scan depth for module detection"),
        non_interactive: bool = typer.Option(
            False, "--non-interactive", "-y", help="Skip confirmation prompts, use defaults"
        ),
        force: bool = typer.Option(False, "--force", help="Overwrite existing files"),
        provider: Optional[str] = typer.Option(
            None, "--provider", help="Dispatch provider: claude_cli (default) or codex_cli"
        ),
    ):
        """Onboard a project into lindy-orchestrator.

        Intelligently detects the project state and adapts:

        - Empty project (no source files) → scaffold mode (LLM-driven, requires description)
        - Existing project without config → init+onboard mode (detect, interview, generate)
        - Already onboarded (has orchestrator.yaml) → re-onboard mode (update config)

        Shows an interactive plan for approval before executing (use -y to skip).
        """
        cwd = Path.cwd()
        console.print(f"[bold]lindy-orchestrate v{__version__}[/] — Onboard\n")

        has_config = _has_config(cwd)
        has_sources = _has_source_files(cwd)

        # Validate provider for scaffold mode (only mode that dispatches to LLM)
        resolved_provider = "claude_cli"
        if not has_config and not has_sources:
            resolved_provider = validate_provider(provider)

        if has_config:
            # Already onboarded → re-onboard/optimize
            console.print("[dim]Detected: orchestrator.yaml exists → re-onboard mode[/]")
            _run_re_onboard_mode(console, cwd, depth, force=force, non_interactive=non_interactive)
        elif has_sources:
            # Existing project without config → init+onboard
            console.print("[dim]Detected: source files found, no config → init+onboard mode[/]")
            _run_init_onboard_mode(
                console, cwd, depth, force=force, non_interactive=non_interactive
            )
        else:
            # Empty project → scaffold mode (LLM-driven)
            console.print("[dim]Detected: empty project → scaffold mode[/]")
            if not description and not file:
                console.print(
                    "[red]Error: Empty project requires a description.[/]\n"
                    'Usage: lindy-orchestrate onboard "Your project description"\n'
                    "   or: lindy-orchestrate onboard --file description.md"
                )
                raise typer.Exit(1)
            _run_scaffold_mode(
                console,
                cwd,
                description,
                file,
                force=force,
                non_interactive=non_interactive,
                provider_name=resolved_provider,
            )
