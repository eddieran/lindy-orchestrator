"""Template for the root-level CLAUDE.md (orchestrator role).

Generates a slim, indexed CLAUDE.md that points to detailed docs in docs/agents/.
"""

from __future__ import annotations

from ...models import DiscoveryContext


def render_root_claude_md(ctx: DiscoveryContext) -> str:
    """Render the root CLAUDE.md — slim indexed version.

    Detailed protocol, conventions, and boundary rules are in docs/agents/.
    """
    # Module table
    mod_rows = []
    for m in ctx.modules:
        tech = ", ".join(m.tech_stack[:3])
        patterns = ", ".join(m.detected_patterns[:3]) if m.detected_patterns else ""
        mod_rows.append(f"| {m.name} | `{m.path}/` | {tech} | {patterns} |")
    module_table = "\n".join(mod_rows)

    # Key files
    key_files = [
        "- `ARCHITECTURE.md` — module topology, layer structure, boundaries",
        "- `docs/agents/protocol.md` — full coordination protocol",
        "- `docs/agents/conventions.md` — coding standards per module",
        "- `docs/agents/boundaries.md` — negative constraints and exceptions",
    ]
    if ctx.coordination_complexity >= 2:
        key_files.insert(1, "- `CONTRACTS.md` — shared interface definitions")
    key_files_str = "\n".join(key_files)

    # Quick rules
    contracts_rule = (
        f"\n3. **CONTRACTS.md** — shared types and interfaces. Never duplicate across modules."
        if ctx.coordination_complexity >= 2
        else ""
    )

    return f"""\
# {ctx.project_name} — Project Orchestrator

> You coordinate modules, you do NOT implement.
> Read STATUS.md files, decompose goals, dispatch tasks, verify quality.

## Modules

| Module | Path | Tech Stack | Patterns |
|--------|------|------------|----------|
{module_table}

## Key Files

{key_files_str}

## Quick Rules

1. **STATUS.md is the message bus** — all cross-module requests go through it.
2. **Scope isolation** — agents only modify files in their own module directory.{contracts_rule}
{3 if ctx.coordination_complexity < 2 else 4}. **Branch delivery** — each task → `{ctx.branch_prefix}/task-{{id}}`, verified by QA gates.

## Session Start

1. Read all module STATUS.md files
2. Check open cross-module requests and blockers
3. Plan, dispatch, verify, report
"""
