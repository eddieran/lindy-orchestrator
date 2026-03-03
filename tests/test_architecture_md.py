"""Tests for ARCHITECTURE.md generation."""

from lindy_orchestrator.discovery.templates.architecture_md import render_architecture_md
from lindy_orchestrator.models import CrossModuleDep, DiscoveryContext, ModuleProfile


def _make_ctx(**kwargs):
    defaults = {
        "project_name": "test-project",
        "project_description": "A test project",
        "root": "/tmp/test",
        "modules": [
            ModuleProfile(
                name="backend",
                path="backend",
                tech_stack=["Python", "FastAPI"],
                detected_patterns=["REST API"],
            ),
        ],
        "cross_deps": [],
        "coordination_complexity": 1,
        "branch_prefix": "af",
        "sensitive_paths": [],
        "qa_requirements": {},
        "git_remote": "",
        "monorepo": False,
    }
    defaults.update(kwargs)
    return DiscoveryContext(**defaults)


def test_basic_architecture_md():
    ctx = _make_ctx()
    result = render_architecture_md(ctx)

    assert "# Architecture — test-project" in result
    assert "map" in result.lower()
    assert "backend/" in result
    assert "Python, FastAPI" in result


def test_module_topology_lists_all_modules():
    ctx = _make_ctx(
        modules=[
            ModuleProfile(name="backend", path="backend", tech_stack=["Python", "FastAPI"]),
            ModuleProfile(name="frontend", path="frontend", tech_stack=["TypeScript", "React"]),
        ]
    )
    result = render_architecture_md(ctx)

    assert "**backend/**" in result
    assert "**frontend/**" in result
    assert "Python, FastAPI" in result
    assert "TypeScript, React" in result


def test_dependency_direction():
    dep = CrossModuleDep(
        from_module="frontend",
        to_module="backend",
        interface_type="api",
        description="REST API on /api/*",
    )
    ctx = _make_ctx(
        cross_deps=[dep],
        modules=[
            ModuleProfile(name="backend", path="backend", tech_stack=["Python", "FastAPI"]),
            ModuleProfile(name="frontend", path="frontend", tech_stack=["TypeScript", "React"]),
        ],
    )
    result = render_architecture_md(ctx)

    assert "## Dependency Direction" in result
    assert "frontend → backend" in result
    assert "via api" in result


def test_negative_boundaries_inferred():
    """Boundaries should include negative constraints (does NOT...)."""
    dep = CrossModuleDep(
        from_module="frontend",
        to_module="backend",
        interface_type="api",
        description="REST API",
    )
    ctx = _make_ctx(
        cross_deps=[dep],
        modules=[
            ModuleProfile(name="backend", path="backend", tech_stack=["Python", "FastAPI"]),
            ModuleProfile(name="frontend", path="frontend", tech_stack=["TypeScript", "React"]),
        ],
    )
    result = render_architecture_md(ctx)

    assert "## Boundaries" in result
    assert "does NOT" in result


def test_layer_structure_for_fastapi():
    ctx = _make_ctx(
        modules=[
            ModuleProfile(name="backend", path="backend", tech_stack=["Python", "FastAPI"]),
        ]
    )
    result = render_architecture_md(ctx)

    assert "## Layer Structure" in result
    assert "models → schemas → services → routes → main" in result


def test_layer_structure_for_react():
    ctx = _make_ctx(
        modules=[
            ModuleProfile(name="frontend", path="frontend", tech_stack=["TypeScript", "React"]),
        ]
    )
    result = render_architecture_md(ctx)

    assert "types → hooks → components → pages → app" in result


def test_shared_definitions_when_complex():
    ctx = _make_ctx(coordination_complexity=2)
    result = render_architecture_md(ctx)

    assert "## Shared Definitions" in result
    assert "CONTRACTS.md" in result


def test_no_shared_definitions_when_simple():
    ctx = _make_ctx(coordination_complexity=1)
    result = render_architecture_md(ctx)

    assert "## Shared Definitions" not in result


def test_sensitive_paths():
    ctx = _make_ctx(sensitive_paths=[".env", "secrets/"])
    result = render_architecture_md(ctx)

    assert "Sensitive Paths" in result
    assert "`.env`" in result
    assert "`secrets/`" in result


def test_module_isolation_boundary():
    """Multi-module projects should have import isolation boundaries."""
    ctx = _make_ctx(
        modules=[
            ModuleProfile(name="backend", path="backend", tech_stack=["Python"]),
            ModuleProfile(name="frontend", path="frontend", tech_stack=["Node.js"]),
        ]
    )
    result = render_architecture_md(ctx)

    assert "does NOT import" in result
