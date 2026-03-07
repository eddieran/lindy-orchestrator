"""Scaffold command — bootstrap lindy orchestration from a project description.

Uses the LLM (via Claude CLI) to analyze a project description and generate
a complete lindy orchestration scaffold (orchestrator.yaml, CLAUDE.md,
ARCHITECTURE.md, STATUS.md, etc.) for a new or empty project.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

from . import __version__
from .cli_helpers import resolve_goal
from .config import DispatcherConfig
from .dispatcher import find_claude_cli
from .models import CrossModuleDep, DiscoveryContext, ModuleProfile
from .providers import create_provider


# ---------------------------------------------------------------------------
# LLM prompt for scaffold analysis
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
    # Strip markdown code fences if present
    cleaned = re.sub(r"```(?:json)?\s*", "", output)
    cleaned = re.sub(r"```\s*$", "", cleaned)

    # Find the JSON object
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
# CLI registration
# ---------------------------------------------------------------------------


def register_scaffold_command(app: typer.Typer, console: Console) -> None:
    """Register the scaffold command on the Typer app."""

    @app.command()
    def scaffold(
        description: Optional[str] = typer.Argument(None, help="Project description text"),
        file: Optional[str] = typer.Option(
            None, "-f", "--file", help="Read description from file (use '-' for stdin)"
        ),
        output_dir: str = typer.Option(
            ".", "-o", "--output-dir", help="Directory to generate files in"
        ),
        force: bool = typer.Option(False, "--force", help="Overwrite existing files"),
        dry_run: bool = typer.Option(
            False, "--dry-run", help="Show what would be generated without writing"
        ),
        non_interactive: bool = typer.Option(
            False, "--non-interactive", "-y", help="Skip confirmation prompts"
        ),
    ):
        """Generate a lindy orchestration scaffold from a project description.

        Provide a natural-language description of the project you want to build.
        The LLM will analyze it and generate orchestrator.yaml, CLAUDE.md,
        ARCHITECTURE.md, STATUS.md, and other organizational files.

        Description can be provided as argument, from a file (--file desc.md),
        or stdin (--file -).
        """
        console.print(f"[bold]lindy-orchestrate v{__version__}[/] -- Scaffold\n")

        # Resolve description text
        desc = resolve_goal(description, file)

        # Check for Claude CLI
        if not find_claude_cli():
            console.print("[red]Error: Claude CLI not found in PATH.[/]")
            console.print("Install: https://docs.anthropic.com/en/docs/claude-code")
            raise typer.Exit(1)

        out_path = Path(output_dir).resolve()
        if not out_path.exists():
            out_path.mkdir(parents=True, exist_ok=True)

        console.print(f"Output: [bold]{out_path}[/]")
        console.print(f"Description: [dim]{desc[:200]}{'...' if len(desc) > 200 else ''}[/]\n")

        # Step 1: Call LLM to analyze description
        console.print("[bold cyan][1/2][/] Analyzing project description with LLM...")
        prompt = _build_scaffold_prompt(desc)

        dispatcher_config = DispatcherConfig(
            timeout_seconds=120,
            permission_mode="bypassPermissions",
        )
        provider = create_provider(dispatcher_config)
        result = provider.dispatch_simple(
            module="scaffold",
            working_dir=out_path,
            prompt=prompt,
        )

        if not result.success:
            console.print(f"[red]LLM call failed: {result.output[:500]}[/]")
            raise typer.Exit(1)

        # Parse LLM response
        try:
            data = parse_scaffold_response(result.output)
        except (json.JSONDecodeError, ValueError) as e:
            console.print(f"[red]Failed to parse LLM response: {e}[/]")
            console.print(f"[dim]Raw output:\n{result.output[:1000]}[/]")
            raise typer.Exit(1)

        ctx = scaffold_response_to_context(data, output_dir=str(out_path))

        # Show what was detected
        console.print(f"\n  Project: [bold]{ctx.project_name}[/]")
        console.print(f"  Modules: [bold]{len(ctx.modules)}[/]")
        for mod in ctx.modules:
            tech = ", ".join(mod.tech_stack) or "unknown"
            console.print(f"    - [bold]{mod.name}[/] ({tech})")
        if ctx.cross_deps:
            console.print(f"  Cross-module deps: [bold]{len(ctx.cross_deps)}[/]")
            for dep in ctx.cross_deps:
                console.print(f"    - {dep.from_module} -> {dep.to_module} ({dep.interface_type})")
        console.print(
            f"  Complexity: [bold]{ctx.coordination_complexity}[/] "
            f"({'loose' if ctx.coordination_complexity == 1 else 'moderate' if ctx.coordination_complexity == 2 else 'tight'})"
        )

        if dry_run:
            console.print("\n[yellow]Dry run — no files written.[/]")
            console.print("\nFiles that would be generated:")
            console.print("  - orchestrator.yaml")
            console.print("  - CLAUDE.md (root)")
            console.print("  - ARCHITECTURE.md")
            for mod in ctx.modules:
                console.print(f"  - {mod.path}/CLAUDE.md")
                console.print(f"  - {mod.path}/STATUS.md")
            if ctx.coordination_complexity >= 2:
                console.print("  - CONTRACTS.md")
            console.print("  - docs/agents/protocol.md")
            console.print("  - docs/agents/conventions.md")
            console.print("  - docs/agents/boundaries.md")
            return

        # Confirm before generating (unless non-interactive)
        if not non_interactive:
            proceed = typer.confirm("\nGenerate scaffold files?", default=True)
            if not proceed:
                console.print("[yellow]Aborted.[/]")
                raise typer.Exit(0)

        # Step 2: Generate artifacts
        console.print("\n[bold cyan][2/2][/] Generating artifacts...\n")

        from .discovery.generator import generate_artifacts

        written = generate_artifacts(ctx, output_dir=out_path, force=force)

        console.print(
            f"\n[bold green]Scaffold complete![/] {len(written)} files generated in {out_path}"
        )
        console.print("\nNext steps:")
        console.print("  1. Review generated files and adjust conventions")
        console.print("  2. Create your module directories and start coding")
        console.print('  3. Run: lindy-orchestrate plan "Your first goal"')
