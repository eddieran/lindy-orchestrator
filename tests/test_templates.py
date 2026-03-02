"""Tests for artifact generation templates."""

from lindy_orchestrator.discovery.templates.contracts_md import render_contracts_md
from lindy_orchestrator.discovery.templates.module_claude_md import render_module_claude_md
from lindy_orchestrator.discovery.templates.root_claude_md import render_root_claude_md
from lindy_orchestrator.models import CrossModuleDep, DiscoveryContext, ModuleProfile


def _make_ctx(**kwargs):
    defaults = {
        "project_name": "my-project",
        "project_description": "A test project",
        "root": "/tmp/test",
        "modules": [],
        "cross_deps": [],
        "coordination_complexity": 1,
        "branch_prefix": "af",
        "sensitive_paths": [".env"],
        "qa_requirements": {},
        "git_remote": "",
        "monorepo": False,
    }
    defaults.update(kwargs)
    return DiscoveryContext(**defaults)


def _make_module(**kwargs):
    defaults = {
        "name": "backend",
        "path": "backend",
        "tech_stack": ["Python", "FastAPI"],
        "dependencies": {"fastapi": ">=0.100"},
        "dir_tree": "backend/\n├── src/\n└── tests/",
        "entry_points": ["src/main.py"],
        "test_commands": ["pytest"],
        "build_commands": ["pip install -e ."],
        "lint_commands": ["ruff check ."],
        "ci_config": "",
        "existing_docs": "",
        "detected_patterns": ["REST API"],
    }
    defaults.update(kwargs)
    return ModuleProfile(**defaults)


class TestRootClaudeMd:
    def test_contains_project_name(self):
        ctx = _make_ctx(project_name="acme-platform")
        result = render_root_claude_md(ctx)
        assert "acme-platform" in result

    def test_contains_module_table(self):
        mod = _make_module()
        ctx = _make_ctx(modules=[mod])
        result = render_root_claude_md(ctx)
        assert "backend" in result
        assert "Python" in result

    def test_contains_orchestrator_role(self):
        ctx = _make_ctx()
        result = render_root_claude_md(ctx)
        assert "Orchestrator" in result
        assert "do NOT implement" in result

    def test_contains_cross_deps(self):
        dep = CrossModuleDep(
            from_module="frontend",
            to_module="backend",
            interface_type="api",
            description="REST calls",
        )
        ctx = _make_ctx(cross_deps=[dep])
        result = render_root_claude_md(ctx)
        assert "frontend" in result
        assert "backend" in result

    def test_contains_sensitive_paths(self):
        ctx = _make_ctx(sensitive_paths=[".env", "secrets/"])
        result = render_root_claude_md(ctx)
        assert ".env" in result
        assert "secrets/" in result


class TestModuleClaudeMd:
    def test_contains_boot_sequence(self):
        mod = _make_module()
        ctx = _make_ctx(modules=[mod])
        result = render_module_claude_md(ctx, mod)
        assert "FIRST ACTION" in result
        assert "STATUS.md" in result

    def test_contains_tech_stack(self):
        mod = _make_module(tech_stack=["Python", "FastAPI", "PostgreSQL"])
        ctx = _make_ctx(modules=[mod])
        result = render_module_claude_md(ctx, mod)
        assert "FastAPI" in result
        assert "PostgreSQL" in result

    def test_contains_dir_layout(self):
        mod = _make_module(dir_tree="backend/\n├── src/\n└── tests/")
        ctx = _make_ctx(modules=[mod])
        result = render_module_claude_md(ctx, mod)
        assert "src/" in result
        assert "tests/" in result

    def test_contains_key_commands(self):
        mod = _make_module(test_commands=["pytest"], build_commands=["pip install -e ."])
        ctx = _make_ctx(modules=[mod])
        result = render_module_claude_md(ctx, mod)
        assert "pytest" in result
        assert "pip install" in result

    def test_contains_scope_boundary(self):
        mod = _make_module()
        ctx = _make_ctx(modules=[mod])
        result = render_module_claude_md(ctx, mod)
        assert "DO NOT" in result
        assert "backend/" in result

    def test_conventions_python(self):
        mod = _make_module(
            tech_stack=["Python"],
            dependencies={"fastapi": ">=0.100", "pydantic": ">=2.0"},
        )
        ctx = _make_ctx(modules=[mod])
        result = render_module_claude_md(ctx, mod)
        assert "type hints" in result
        assert "Pydantic" in result

    def test_cross_module_section(self):
        dep = CrossModuleDep(
            from_module="frontend",
            to_module="backend",
            interface_type="api",
            description="REST calls",
        )
        mod = _make_module(name="backend")
        ctx = _make_ctx(modules=[mod, _make_module(name="frontend")], cross_deps=[dep])
        result = render_module_claude_md(ctx, mod)
        assert "Consumes" in result
        assert "frontend" in result


class TestContractsMd:
    def test_contains_header(self):
        ctx = _make_ctx(project_name="my-platform")
        result = render_contracts_md(ctx)
        assert "my-platform" in result
        assert "Contracts" in result

    def test_api_section(self):
        dep = CrossModuleDep(
            from_module="frontend",
            to_module="backend",
            interface_type="api",
            description="User endpoints",
        )
        ctx = _make_ctx(cross_deps=[dep])
        result = render_contracts_md(ctx)
        assert "API Contracts" in result
        assert "User endpoints" in result

    def test_generic_section_when_no_deps(self):
        ctx = _make_ctx(modules=[_make_module()])
        result = render_contracts_md(ctx)
        assert "Interfaces" in result

    def test_change_protocol(self):
        ctx = _make_ctx()
        result = render_contracts_md(ctx)
        assert "Change Protocol" in result
