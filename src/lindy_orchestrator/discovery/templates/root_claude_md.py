"""Template for the root-level CLAUDE.md (orchestrator role)."""

from __future__ import annotations

from ...models import DiscoveryContext


def render_root_claude_md(ctx: DiscoveryContext) -> str:
    """Render the root CLAUDE.md for the project orchestrator."""
    # Module table
    mod_rows = []
    for m in ctx.modules:
        tech = ", ".join(m.tech_stack[:3])
        patterns = ", ".join(m.detected_patterns[:3]) if m.detected_patterns else ""
        mod_rows.append(f"| {m.name} | `{m.path}/` | {tech} | {patterns} |")
    module_table = "\n".join(mod_rows)

    # Data flow
    dep_lines = []
    if ctx.cross_deps:
        for d in ctx.cross_deps:
            arrow = f"{d.from_module} → {d.to_module}"
            desc = f" ({d.description})" if d.description else ""
            dep_lines.append(f"- {arrow}{desc}")
    else:
        dep_lines.append("- (Define cross-module dependencies here)")
    data_flow = "\n".join(dep_lines)

    # Sensitive paths
    sensitive = (
        "\n".join(f"- `{p}`" for p in ctx.sensitive_paths) if ctx.sensitive_paths else "- (none)"
    )

    return f"""\
# {ctx.project_name} — Project Orchestrator

> You are the Project Orchestrator. You coordinate modules, you do NOT implement.
> Your job is to read all module STATUS.md files, decompose goals into module-level
> tasks, dispatch them, and verify quality. You never write application code directly.

## Project Overview

{ctx.project_description}

## Modules

| Module | Path | Tech Stack | Patterns |
|--------|------|------------|----------|
{module_table}

## Data Flow

{data_flow}

## Coordination Protocol

1. **STATUS.md as message bus** — Each module has a STATUS.md tracking work state.
   Cross-module requests go through the "Cross-Module Requests" table.
2. **Scope isolation** — Agents must NOT modify files outside their module directory.
   If they need work from another module, they create a Cross-Module Request.
3. **Branch-based delivery** — Each task produces a branch: `{ctx.branch_prefix}/task-{{id}}`.
   Agents commit and push to their branch.
4. **QA gates** — Every task is verified by quality checks before marking complete.
{"5. **CONTRACTS.md** — Shared interface definitions live in CONTRACTS.md at project root." if ctx.coordination_complexity >= 2 else ""}

## Sensitive Paths (DO NOT modify)

{sensitive}

## Session Protocol

1. Read all module STATUS.md files
2. Check for open cross-module requests and blockers
3. Plan and dispatch tasks based on the current goal
4. Verify results through QA gates
5. Generate a completion report
"""
