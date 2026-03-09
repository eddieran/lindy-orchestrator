"""Template for per-module CLAUDE.md (agent instructions)."""

from __future__ import annotations

from ...models import CrossModuleDep, DiscoveryContext, ModuleProfile


def render_module_claude_md(
    ctx: DiscoveryContext,
    module: ModuleProfile,
) -> str:
    """Render a module-level CLAUDE.md with the 8-section Lindy pattern."""
    sections = [
        _header(module),
        _boot_sequence(),
        _role(module),
        _tech_stack(module),
        _dir_layout(module),
        _key_commands(module),
        _conventions(module),
        _cross_module(ctx, module),
        _scope_boundary(ctx, module),
    ]
    return "\n".join(s for s in sections if s)


def _header(module: ModuleProfile) -> str:
    tech = ", ".join(module.tech_stack[:3])
    return f"# {module.name.title()} Agent\n\n{tech} module.\n"


def _boot_sequence() -> str:
    return """\
> **FIRST ACTION ON EVERY SESSION:** Read the STATUS.md content injected into your prompt.
> Check "Cross-Module Requests" for OPEN items — those are your assigned tasks.
> Check "Active Work" for in-progress items from previous sessions.
> Do this BEFORE anything else.
"""


def _role(module: ModuleProfile) -> str:
    patterns = (
        ", ".join(module.detected_patterns) if module.detected_patterns else "general development"
    )
    return f"""\
## Role

You are the {module.name} agent. You own all code and configuration under `{module.path}/`.
Primary focus: {patterns}.
"""


def _tech_stack(module: ModuleProfile) -> str:
    if not module.tech_stack:
        return ""
    items = "\n".join(f"- {t}" for t in module.tech_stack)
    return f"""\
## Tech Stack

{items}
"""


def _dir_layout(module: ModuleProfile) -> str:
    if not module.dir_tree:
        return ""
    return f"""\
## Directory Layout

```
{module.dir_tree}
```
"""


def _key_commands(module: ModuleProfile) -> str:
    all_cmds = []

    if module.build_commands:
        all_cmds.append("# Build")
        all_cmds.extend(module.build_commands)
        all_cmds.append("")

    if module.test_commands:
        all_cmds.append("# Test")
        all_cmds.extend(module.test_commands)
        all_cmds.append("")

    if module.lint_commands:
        all_cmds.append("# Lint")
        all_cmds.extend(module.lint_commands)
        all_cmds.append("")

    if not all_cmds:
        return ""

    cmds = "\n".join(all_cmds).rstrip()
    return f"""\
## Key Commands

```bash
{cmds}
```
"""


def _conventions(module: ModuleProfile) -> str:
    """Generate conventions section based on detected tech."""
    rules: list[str] = []

    tech_lower = {t.lower() for t in module.tech_stack}
    dep_names = set(module.dependencies.keys())

    # Python conventions
    if "python" in tech_lower:
        rules.append("- Use type hints for all function signatures")
        rules.append("- Follow PEP 8 naming conventions")
        if "pydantic" in dep_names:
            rules.append("- Use Pydantic models for data validation")
        if "sqlalchemy" in dep_names:
            rules.append("- Use SQLAlchemy 2.0 style (select() not legacy query())")
        if "fastapi" in dep_names:
            rules.append("- Use async def for route handlers where possible")

    # TypeScript/Node conventions
    if "typescript" in tech_lower or "node.js" in tech_lower:
        if "typescript" in dep_names:
            rules.append("- Use strict TypeScript — no `any` types")
        if "react" in dep_names:
            rules.append("- Use functional components with hooks")
        if "next" in dep_names or "next.js" in tech_lower:
            rules.append("- Use App Router conventions (app/ directory)")

    # Rust conventions
    if "rust" in tech_lower:
        rules.append("- Use `Result<T, E>` for error handling, avoid unwrap()")
        rules.append("- Prefer `&str` over `String` in function parameters")

    # Go conventions
    if "go" in tech_lower:
        rules.append("- Return errors, don't panic")
        rules.append("- Use context.Context for cancellation and timeouts")

    if not rules:
        return ""

    items = "\n".join(rules)
    return f"""\
## Conventions

{items}
"""


def _cross_module(ctx: DiscoveryContext, module: ModuleProfile) -> str:
    """Generate cross-module interfaces section."""
    if len(ctx.modules) <= 1:
        return ""

    consumes: list[CrossModuleDep] = [d for d in ctx.cross_deps if d.to_module == module.name]
    produces: list[CrossModuleDep] = [d for d in ctx.cross_deps if d.from_module == module.name]

    if not consumes and not produces:
        return ""

    parts = ["## Cross-Module Interfaces\n"]

    if consumes:
        parts.append("### Consumes (from other modules)")
        for d in consumes:
            desc = f": {d.description}" if d.description else ""
            parts.append(f"- From **{d.from_module}** ({d.interface_type}){desc}")
        parts.append("")

    if produces:
        parts.append("### Produces (for other modules)")
        for d in produces:
            desc = f": {d.description}" if d.description else ""
            parts.append(f"- To **{d.to_module}** ({d.interface_type}){desc}")
        parts.append("")

    return "\n".join(parts)


def _scope_boundary(ctx: DiscoveryContext, module: ModuleProfile) -> str:
    """Generate the scope boundary section."""
    sensitive = ""
    if ctx.sensitive_paths:
        paths = ", ".join(f"`{p}`" for p in ctx.sensitive_paths)
        sensitive = f"\n\nNever modify these files: {paths}"

    return f"""\
## Scope Boundary

You own ONLY files under `{module.path}/`.

**DO NOT** directly modify files in other modules. If you need work from another
module, create a Cross-Module Request in your STATUS.md under the
"Cross-Module Requests" section.

When you complete work, update your STATUS.md:
- Move tasks to "Completed (Recent)"
- Update "Active Work" status
- Record any deliverables in "Cross-Module Deliverables"{sensitive}
"""
