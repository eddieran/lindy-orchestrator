"""Unified onboard command — merges init, scaffold, and onboard into one entry point.

Detects project state and adapts behavior:
- Empty project (no source files, no config) → scaffold mode (LLM-driven)
- Existing project without orchestrator.yaml → init+onboard mode (detect, interview, generate)
- Already onboarded (has orchestrator.yaml) → re-onboard mode
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

from . import __version__
from .cli_helpers import resolve_goal, validate_provider
from .cli_onboard_helpers import (
    _build_scaffold_prompt,
    _has_config,
    _has_source_files,
    _show_plan,
    parse_scaffold_response,
    scaffold_response_to_context,
)
from .config import CONFIG_FILENAME, DispatcherConfig
from .providers import create_provider


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
