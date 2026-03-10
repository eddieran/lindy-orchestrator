"""Templates for docs/agents/ — detailed agent reference documents.

These are split out from the root CLAUDE.md to keep it concise.
The root CLAUDE.md contains pointers to these files.
"""

from __future__ import annotations

from ...models import DiscoveryContext


def render_agent_docs(ctx: DiscoveryContext) -> dict[str, str]:
    """Generate docs/agents/ detailed documents.

    Returns a mapping of filename → content.
    """
    return {
        "protocol.md": _render_protocol(ctx),
        "conventions.md": _render_conventions(ctx),
        "boundaries.md": _render_boundaries(ctx),
    }


def _render_protocol(ctx: DiscoveryContext) -> str:
    """Full coordination protocol document."""
    contracts_note = ""
    if ctx.coordination_complexity >= 2:
        contracts_note = (
            "\n## CONTRACTS.md\n\n"
            "Shared interface definitions live in `.orchestrator/contracts.md`.\n"
            "All cross-module data shapes, API contracts, and shared types are defined there.\n"
            "Never duplicate type definitions across modules — reference `.orchestrator/contracts.md`.\n"
        )

    return f"""\
# Coordination Protocol — {ctx.project_name}

> Detailed rules for multi-agent coordination. The root CLAUDE.md
> contains a summary; this document is the full reference.

## STATUS.md as Message Bus

Each module has a STATUS.md file at `.orchestrator/status/{{module}}.md` that tracks:
- **Module Metadata**: health (GREEN/YELLOW/RED), last update, session ID
- **Active Work**: current tasks with status and blockers
- **Completed (Recent)**: recently finished tasks
- **Cross-Module Requests**: OPEN/IN_PROGRESS/DONE requests between modules
- **Cross-Module Deliverables**: completed outputs for other modules
- **Key Metrics**: module-specific health indicators
- **Blockers**: issues that prevent progress

### Request Flow
1. Module A needs work from Module B → A creates a Cross-Module Request in A's STATUS.md
2. Orchestrator picks up open requests during planning
3. Module B executes the request and records a deliverable
4. Module A reads the deliverable and marks the request DONE

## Branch-Based Delivery

- Each task produces a branch: `{ctx.branch_prefix}/task-{{id}}`
- Agents commit and push to their branch
- The orchestrator verifies branch existence and commit count
- Branches are merged after QA gates pass

## QA Gates

Every task is verified by quality checks before marking complete:
- **structural_check**: file size limits, sensitive file detection, import boundaries
- **layer_check**: intra-module layer ordering (parsed from ARCHITECTURE.md)
- **ci_check**: CI pipeline pass/fail
- **command_check**: custom commands (test suites, linters)
- **agent_check**: dispatches a QA agent for complex validation

QA failures trigger automatic retry with structured feedback.
Maximum retries: configurable per project (default: 2).

## Cross-Module Request Protocol

When you need work from another module:
1. Add an entry to your `.orchestrator/status/{{module}}.md` "Cross-Module Requests" table
2. Set status to OPEN
3. Include priority (P0=critical, P1=high, P2=normal)
4. The orchestrator will pick it up in the next planning cycle

Do NOT directly modify files in other modules.
{contracts_note}
## ARCHITECTURE.md

The structural map at `.orchestrator/architecture.md` defines:
- Module topology and tech stacks
- Dependency directions between modules
- Negative boundaries (what does NOT belong where)
- Layer structure per module

Read `.orchestrator/architecture.md` before planning any cross-module work.
"""


def _render_conventions(ctx: DiscoveryContext) -> str:
    """Coding conventions document based on detected tech stacks."""
    sections: list[str] = [
        f"# Coding Conventions — {ctx.project_name}",
        "",
        "> Auto-generated from detected tech stacks. Customize as needed.",
        "",
    ]

    for mod in ctx.modules:
        tech_lower = {t.lower() for t in mod.tech_stack}
        dep_names = set(mod.dependencies.keys())
        rules: list[str] = []

        # Python conventions
        if "python" in tech_lower:
            rules.append("- Use type hints for all function signatures")
            rules.append("- Follow PEP 8 naming conventions")
            rules.append("- Prefer `pathlib.Path` over `os.path`")
            if "pydantic" in dep_names:
                rules.append("- Use Pydantic models for data validation")
                rules.append("- Use `model_validate()` not deprecated `parse_obj()`")
            if "sqlalchemy" in dep_names:
                rules.append("- Use SQLAlchemy 2.0 style (`select()` not legacy `query()`)")
            if "fastapi" in dep_names:
                rules.append("- Use `async def` for route handlers where possible")
                rules.append("- Use dependency injection for shared services")

        # TypeScript/Node conventions
        if "typescript" in tech_lower or "node.js" in tech_lower:
            if "typescript" in dep_names:
                rules.append("- Use strict TypeScript — no `any` types")
            if "react" in dep_names:
                rules.append("- Use functional components with hooks")
                rules.append("- Prefer named exports over default exports")
            if "next" in dep_names or "next.js" in tech_lower:
                rules.append("- Use App Router conventions (`app/` directory)")

        # Rust conventions
        if "rust" in tech_lower:
            rules.append("- Use `Result<T, E>` for error handling, avoid `unwrap()`")
            rules.append("- Prefer `&str` over `String` in function parameters")

        # Go conventions
        if "go" in tech_lower:
            rules.append("- Return errors, don't panic")
            rules.append("- Use `context.Context` for cancellation and timeouts")

        if rules:
            sections.append(f"## {mod.name}")
            sections.append("")
            sections.extend(rules)
            sections.append("")

    if len(sections) <= 4:
        sections.append("(No language-specific conventions detected. Add your own.)")
        sections.append("")

    return "\n".join(sections)


def _render_boundaries(ctx: DiscoveryContext) -> str:
    """Boundary rules document — negative constraints and exceptions."""
    sections: list[str] = [
        f"# Module Boundaries — {ctx.project_name}",
        "",
        "> Negative constraints: what does NOT belong where.",
        "> These rules prevent scope creep and cross-module pollution.",
        "",
    ]

    # Module isolation
    sections.append("## Module Isolation")
    sections.append("")
    if len(ctx.modules) > 1:
        for mod in ctx.modules:
            other_names = [m.name for m in ctx.modules if m.name != mod.name]
            if other_names:
                others = ", ".join(f"`{n}/`" for n in other_names)
                sections.append(f"- `{mod.name}/` does NOT import from {others}")
    else:
        sections.append("- Single module project — no cross-module concerns")
    sections.append("")

    # Sensitive paths
    if ctx.sensitive_paths:
        sections.append("## Sensitive Paths (NEVER modify)")
        sections.append("")
        for p in ctx.sensitive_paths:
            sections.append(f"- `{p}`")
        sections.append("")

    # Cross-module communication
    if len(ctx.modules) > 1:
        sections.append("## Cross-Module Communication")
        sections.append("")
        sections.append(
            "All cross-module communication goes through `.orchestrator/status/` requests "
            "and `.orchestrator/contracts.md` interfaces."
        )
        sections.append("Direct file access across module boundaries is prohibited.")
        sections.append("")

        if ctx.cross_deps:
            sections.append("### Allowed Interfaces")
            sections.append("")
            for dep in ctx.cross_deps:
                desc = f": {dep.description}" if dep.description else ""
                iface = dep.interface_type or "unspecified"
                sections.append(f"- {dep.from_module} → {dep.to_module} ({iface}){desc}")
            sections.append("")

    # Exceptions
    sections.append("## Exceptions")
    sections.append("")
    sections.append("The following are allowed exceptions to boundary rules:")
    sections.append("- Shared config files at project root (e.g., `.env`, `docker-compose.yml`)")
    sections.append("- CI/CD pipeline files that reference multiple modules")
    sections.append("- Documentation files (`docs/`, `README.md`)")
    sections.append("")

    return "\n".join(sections)
