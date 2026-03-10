"""Template for ARCHITECTURE.md — a map, not a manual.

Generates a structural overview that tells agents what exists WHERE
and, critically, what does NOT exist in each module. Negative boundaries
constrain the solution space more effectively than positive docs.
"""

from __future__ import annotations

from ...models import DiscoveryContext, ModuleProfile


def render_architecture_md(ctx: DiscoveryContext) -> str:
    """Render ARCHITECTURE.md from the discovery context."""
    sections = [
        f"# Architecture — {ctx.project_name}",
        "",
        "> This is a **map**, not a manual. It tells you what exists where,",
        "> how modules relate, and — critically — what does NOT belong where.",
        "",
    ]

    # Module Topology
    sections.append("## Module Topology")
    sections.append("")
    for mod in ctx.modules:
        tech = ", ".join(mod.tech_stack) if mod.tech_stack else "unknown"
        patterns = f" — {', '.join(mod.detected_patterns)}" if mod.detected_patterns else ""
        sections.append(f"- **{mod.name}/** (`{mod.path}/`) → {tech}{patterns}")
    sections.append("")

    # Dependency Direction
    if ctx.cross_deps:
        sections.append("## Dependency Direction")
        sections.append("")
        for dep in ctx.cross_deps:
            desc = f" ({dep.description})" if dep.description else ""
            iface = f" via {dep.interface_type}" if dep.interface_type else ""
            sections.append(f"- {dep.from_module} → {dep.to_module}{iface}{desc}")
        sections.append("")

    # Boundaries (negative constraints)
    sections.append("## Boundaries")
    sections.append("")
    boundaries = _infer_boundaries(ctx)
    for boundary in boundaries:
        sections.append(f"- {boundary}")
    sections.append("")

    # Layer Structure (per module)
    layer_section = _build_layer_structure(ctx.modules)
    if layer_section:
        sections.append("## Layer Structure")
        sections.append("")
        for line in layer_section:
            sections.append(line)
        sections.append("")

    # Shared Definitions
    if ctx.coordination_complexity >= 2:
        sections.append("## Shared Definitions")
        sections.append("")
        sections.append("- Shared types and interfaces are defined in `.orchestrator/contracts.md`")
        sections.append("- Never duplicate type definitions across modules")
        sections.append("")

    # Sensitive Paths
    if ctx.sensitive_paths:
        sections.append("## Sensitive Paths (DO NOT commit)")
        sections.append("")
        for p in ctx.sensitive_paths:
            sections.append(f"- `{p}`")
        sections.append("")

    return "\n".join(sections)


def _infer_boundaries(ctx: DiscoveryContext) -> list[str]:
    """Infer negative boundary constraints from module profiles."""
    boundaries = []

    tech_map: dict[str, list[str]] = {}
    for mod in ctx.modules:
        for tech in mod.tech_stack:
            tech_map.setdefault(tech.lower(), []).append(mod.name)

    # Cross-module boundary rules
    for dep in ctx.cross_deps:
        if dep.interface_type == "api":
            boundaries.append(
                f"`{dep.from_module}/` does NOT call `{dep.to_module}/` "
                f"internals — all communication via API"
            )
        elif dep.interface_type == "database":
            boundaries.append(
                f"`{dep.from_module}/` does NOT access `{dep.to_module}/` "
                f"database directly — use the provided interface"
            )

    # Module isolation rules
    if len(ctx.modules) > 1:
        for mod in ctx.modules:
            other_names = [m.name for m in ctx.modules if m.name != mod.name]
            if other_names:
                boundaries.append(
                    f"`{mod.name}/` does NOT import from "
                    + ", ".join(f"`{n}/`" for n in other_names)
                    + " — use .orchestrator/contracts.md interfaces"
                )

    # Tech-specific boundaries
    has_backend = any(
        t in tech_map
        for t in ["python", "fastapi", "django", "flask", "express", "go", "java", "rust"]
    )
    has_frontend = any(
        t in tech_map for t in ["react", "next.js", "vue", "angular", "typescript", "svelte"]
    )

    if has_backend and has_frontend:
        backend_mods = []
        frontend_mods = []
        for mod in ctx.modules:
            tech_lower = [t.lower() for t in mod.tech_stack]
            if any(t in tech_lower for t in ["react", "next.js", "vue", "angular", "svelte"]):
                frontend_mods.append(mod.name)
            elif any(
                t in tech_lower for t in ["fastapi", "django", "flask", "express", "gin", "spring"]
            ):
                backend_mods.append(mod.name)

        for fe in frontend_mods:
            boundaries.append(f"`{fe}/` does NOT contain server-side logic — all data via API")
        for be in backend_mods:
            boundaries.append(f"`{be}/` does NOT serve HTML — JSON API only")

    if not boundaries:
        boundaries.append("Each module is self-contained; do not create cross-module imports")

    return boundaries


def _build_layer_structure(modules: list[ModuleProfile]) -> list[str]:
    """Infer layer structure from detected patterns and tech stack."""
    lines: list[str] = []
    for mod in modules:
        tech_lower = [t.lower() for t in mod.tech_stack]
        layers: str = ""

        if any(t in tech_lower for t in ["fastapi", "flask"]):
            layers = "models → schemas → services → routes → main"
        elif "django" in tech_lower:
            layers = "models → serializers → views → urls → wsgi"
        elif "express" in tech_lower:
            layers = "models → middleware → routes → controllers → app"
        elif any(t in tech_lower for t in ["react", "next.js"]):
            layers = "types → hooks → components → pages → app"
        elif "vue" in tech_lower:
            layers = "types → composables → components → views → router"
        elif "spring" in tech_lower:
            layers = "entities → repositories → services → controllers → application"

        if layers:
            lines.append(f"- **{mod.name}/**: {layers}")

    return lines
