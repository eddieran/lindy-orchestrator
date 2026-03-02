"""Interactive project discovery interview.

Asks targeted questions based on the auto-analysis results.
Supports --non-interactive mode with sensible defaults.
"""

from __future__ import annotations


from rich.console import Console
from rich.prompt import Confirm, IntPrompt, Prompt

from ..models import CrossModuleDep, DiscoveryContext, ModuleProfile, ProjectProfile

console = Console()


def run_interview(
    profile: ProjectProfile,
    non_interactive: bool = False,
) -> DiscoveryContext:
    """Run the interactive discovery interview.

    Uses the auto-analyzed ProjectProfile to skip already-answered questions
    and focus on what needs human input.

    In non-interactive mode, uses sensible defaults for everything.
    """
    console.print("\n[bold cyan]=== Project Discovery ===[/]\n")

    # Show what was auto-detected
    _show_detected(profile)

    # Q1: Project description
    project_desc = _ask_project_description(profile, non_interactive)

    # Q2: Module roles
    modules = _ask_module_roles(profile, non_interactive)

    # Q3: Cross-module dependencies (skip if single module)
    cross_deps: list[CrossModuleDep] = []
    if len(modules) > 1:
        cross_deps = _ask_cross_deps(modules, non_interactive)

    # Q4: QA requirements per module
    qa_reqs = _ask_qa_requirements(modules, non_interactive)

    # Q5: Sensitive paths
    sensitive = _ask_sensitive_paths(non_interactive)

    # Q6: Coordination complexity (skip if single module)
    complexity = 1
    if len(modules) > 1:
        complexity = _ask_coordination_complexity(non_interactive)

    # Q7: Branch prefix
    branch_prefix = _ask_branch_prefix(non_interactive)

    console.print("\n[bold green]Discovery complete![/]\n")

    return DiscoveryContext(
        project_name=profile.name,
        project_description=project_desc,
        root=profile.root,
        modules=modules,
        cross_deps=cross_deps,
        coordination_complexity=complexity,
        branch_prefix=branch_prefix,
        sensitive_paths=sensitive,
        qa_requirements=qa_reqs,
        git_remote=profile.git_remote,
        monorepo=profile.monorepo,
    )


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------


def _show_detected(profile: ProjectProfile) -> None:
    """Show what was auto-detected."""
    console.print(f"[bold]Project:[/] {profile.name}")
    console.print(f"[bold]Root:[/] {profile.root}")

    if profile.modules:
        mod_list = ", ".join(
            f"[bold]{m.name}[/] ({', '.join(m.tech_stack)})" for m in profile.modules
        )
        console.print(f"[bold]Modules:[/] {mod_list}")

    if profile.detected_ci:
        console.print(f"[bold]CI:[/] {profile.detected_ci}")

    if profile.git_remote:
        console.print(f"[bold]Git:[/] {profile.git_remote}")

    console.print()


# ---------------------------------------------------------------------------
# Questions
# ---------------------------------------------------------------------------


def _ask_project_description(profile: ProjectProfile, non_interactive: bool) -> str:
    """Ask about the project's primary purpose."""
    # Check if existing docs provide a description
    existing_hint = ""
    for mod in profile.modules:
        if mod.existing_docs:
            # Extract first meaningful line from README
            for line in mod.existing_docs.splitlines():
                line = line.strip()
                if (
                    line
                    and not line.startswith("=")
                    and not line.startswith("#")
                    and len(line) > 20
                ):
                    existing_hint = line[:200]
                    break
            if existing_hint:
                break

    if non_interactive:
        if existing_hint:
            console.print(f'  [dim]→ Q1: Found description in docs: "{existing_hint}"[/]')
            return existing_hint
        tech_summary = ", ".join(t for m in profile.modules for t in m.tech_stack[:2])
        desc = f"A {tech_summary} project" if tech_summary else f"Project: {profile.name}"
        console.print(f'  [dim]→ Q1: No description in docs, using tech summary: "{desc}"[/]')
        return desc

    if existing_hint:
        console.print(f"[dim]Detected description: {existing_hint}[/]")
        if Confirm.ask("Use this description?", default=True):
            return existing_hint

    return Prompt.ask(
        "[bold]Q1.[/] What is this project's primary purpose?",
        default=f"Project: {profile.name}",
    )


def _ask_module_roles(profile: ProjectProfile, non_interactive: bool) -> list[ModuleProfile]:
    """Ask about each module's role (or confirm auto-detected info)."""
    modules = list(profile.modules)

    if non_interactive:
        for mod in modules:
            tech = ", ".join(mod.tech_stack) or "unknown"
            patterns = ", ".join(mod.detected_patterns) if mod.detected_patterns else "none"
            console.print(
                f"  [dim]→ Q2: Module [bold]{mod.name}[/dim][dim] — tech: {tech}, patterns: {patterns}[/]"
            )
        return modules

    console.print("[bold]Q2.[/] Module roles:")
    for mod in modules:
        tech = ", ".join(mod.tech_stack)
        patterns = ", ".join(mod.detected_patterns) if mod.detected_patterns else "none detected"
        console.print(f"  [bold]{mod.name}[/] — Tech: {tech}, Patterns: {patterns}")

        # Only ask if patterns are empty (not much was detected)
        if not mod.detected_patterns:
            desc = Prompt.ask(
                f"  What is [bold]{mod.name}[/]'s primary responsibility?",
                default="(skip)",
            )
            if desc != "(skip)":
                mod.detected_patterns = [desc]

    return modules


def _ask_cross_deps(modules: list[ModuleProfile], non_interactive: bool) -> list[CrossModuleDep]:
    """Ask about cross-module dependencies."""
    if non_interactive:
        console.print(
            "  [dim]→ Q3: No cross-module deps in non-interactive mode (specify manually in orchestrator.yaml if needed)[/]"
        )
        return []

    console.print("\n[bold]Q3.[/] Cross-module dependencies")
    console.print("[dim]  Format: from_module -> to_module : description[/]")
    console.print("[dim]  Example: frontend -> backend : REST API calls[/]")
    console.print("[dim]  Enter empty line when done.[/]")

    deps: list[CrossModuleDep] = []
    module_names = {m.name for m in modules}

    while True:
        raw = Prompt.ask("  Dependency", default="")
        if not raw:
            break

        # Parse "from -> to : description"
        parts = raw.split("->")
        if len(parts) != 2:
            console.print("  [yellow]Format: from -> to : description[/]")
            continue

        from_mod = parts[0].strip()
        rest = parts[1].strip()

        desc = ""
        if ":" in rest:
            to_mod, desc = rest.split(":", 1)
            to_mod = to_mod.strip()
            desc = desc.strip()
        else:
            to_mod = rest

        if from_mod not in module_names or to_mod not in module_names:
            available = ", ".join(sorted(module_names))
            console.print(f"  [yellow]Unknown module. Available: {available}[/]")
            continue

        # Guess interface type
        itype = _guess_interface_type(desc)
        deps.append(
            CrossModuleDep(
                from_module=from_mod,
                to_module=to_mod,
                interface_type=itype,
                description=desc,
            )
        )

    return deps


def _guess_interface_type(desc: str) -> str:
    """Guess the interface type from a description."""
    desc_lower = desc.lower()
    if any(t in desc_lower for t in ("api", "rest", "http", "endpoint", "graphql")):
        return "api"
    if any(t in desc_lower for t in ("file", "csv", "parquet", "json file")):
        return "file"
    if any(t in desc_lower for t in ("database", "db", "sql", "table")):
        return "database"
    if any(t in desc_lower for t in ("env", "environment", "config")):
        return "env_var"
    if any(t in desc_lower for t in ("queue", "kafka", "rabbitmq", "redis pub")):
        return "message_queue"
    return "api"


def _ask_qa_requirements(
    modules: list[ModuleProfile], non_interactive: bool
) -> dict[str, list[str]]:
    """Ask about QA requirements per module, or confirm auto-detected ones."""
    qa_reqs: dict[str, list[str]] = {}

    for mod in modules:
        all_cmds = mod.test_commands + mod.lint_commands
        if all_cmds:
            qa_reqs[mod.name] = all_cmds
            if non_interactive:
                cmd_list = ", ".join(all_cmds)
                console.print(
                    f"  [dim]→ Q4: QA for [bold]{mod.name}[/dim][dim]: using detected commands [{cmd_list}][/]"
                )
            else:
                cmd_list = ", ".join(all_cmds)
                console.print(f"\n[bold]Q4.[/] QA for [bold]{mod.name}[/]: detected [{cmd_list}]")
                if not Confirm.ask("  Use these?", default=True):
                    custom = Prompt.ask(
                        "  Enter QA commands (comma-separated)",
                        default=cmd_list,
                    )
                    qa_reqs[mod.name] = [c.strip() for c in custom.split(",") if c.strip()]
        else:
            if non_interactive:
                console.print(
                    f"  [dim]→ Q4: QA for [bold]{mod.name}[/dim][dim]: no test/lint commands detected[/]"
                )
            else:
                console.print(f"\n[bold]Q4.[/] QA for [bold]{mod.name}[/]: none detected")
                custom = Prompt.ask(
                    "  Enter QA commands (comma-separated, or empty to skip)",
                    default="",
                )
                if custom:
                    qa_reqs[mod.name] = [c.strip() for c in custom.split(",") if c.strip()]

    return qa_reqs


def _ask_sensitive_paths(non_interactive: bool) -> list[str]:
    """Ask about paths that should never be modified by agents."""
    defaults = [".env", ".env.*", "*.key", "*.pem"]

    if non_interactive:
        console.print(f"  [dim]→ Q5: Using default sensitive paths: {', '.join(defaults)}[/]")
        return defaults

    console.print("\n[bold]Q5.[/] Sensitive paths (agents should NEVER modify)")
    console.print(f"[dim]  Defaults: {', '.join(defaults)}[/]")
    extra = Prompt.ask("  Additional paths (comma-separated, or empty)", default="")

    if extra:
        return defaults + [p.strip() for p in extra.split(",") if p.strip()]
    return defaults


def _ask_coordination_complexity(non_interactive: bool) -> int:
    """Ask about how tightly coupled the modules are."""
    if non_interactive:
        console.print("  [dim]→ Q6: Using moderate coupling (level 2) as default[/]")
        return 2  # moderate default

    console.print("\n[bold]Q6.[/] How tightly coupled are the modules?")
    console.print("  [1] Loosely — mostly independent, occasional contracts")
    console.print("  [2] Moderate — shared schemas, regular cross-module features")
    console.print("  [3] Tight — changes often span multiple modules simultaneously")

    return IntPrompt.ask("  Level", default=2, choices=["1", "2", "3"])


def _ask_branch_prefix(non_interactive: bool) -> str:
    """Ask about the branch naming prefix."""
    if non_interactive:
        console.print('  [dim]→ Q7: Using default branch prefix: "af"[/]')
        return "af"

    return Prompt.ask(
        "\n[bold]Q7.[/] Branch prefix for orchestrated work",
        default="af",
    )
