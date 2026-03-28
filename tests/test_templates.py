"""Tests for artifact generation templates."""

from lindy_orchestrator.discovery.templates.agent_docs import render_agent_docs
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

    def test_contains_key_files_pointers(self):
        ctx = _make_ctx()
        result = render_root_claude_md(ctx)
        assert "Key Files" in result
        assert ".orchestrator/docs/protocol.md" in result
        assert ".orchestrator/docs/conventions.md" in result
        assert ".orchestrator/docs/boundaries.md" in result

    def test_slim_under_50_lines(self):
        mod = _make_module()
        ctx = _make_ctx(modules=[mod])
        result = render_root_claude_md(ctx)
        line_count = len(result.strip().splitlines())
        assert line_count < 50, f"CLAUDE.md is {line_count} lines, expected < 50"

    def test_contracts_pointer_when_complex(self):
        ctx = _make_ctx(coordination_complexity=2)
        result = render_root_claude_md(ctx)
        assert ".orchestrator/contracts.md" in result

    def test_contains_quick_rules(self):
        ctx = _make_ctx()
        result = render_root_claude_md(ctx)
        assert "Quick Rules" in result
        assert "STATUS.md" in result
        assert "message bus" in result

    def test_contains_session_start(self):
        ctx = _make_ctx()
        result = render_root_claude_md(ctx)
        assert "Session Start" in result


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


# ---------------------------------------------------------------------------
# Agent docs (docs/agents/)
# ---------------------------------------------------------------------------


class TestAgentDocs:
    def test_returns_three_files(self):
        ctx = _make_ctx()
        docs = render_agent_docs(ctx)
        assert set(docs.keys()) == {"protocol.md", "conventions.md", "boundaries.md"}

    def test_protocol_contains_status_md_bus(self):
        ctx = _make_ctx(project_name="acme")
        docs = render_agent_docs(ctx)
        assert "STATUS.md" in docs["protocol.md"]
        assert "Message Bus" in docs["protocol.md"]
        assert "acme" in docs["protocol.md"]

    def test_protocol_contains_branch_delivery(self):
        ctx = _make_ctx(branch_prefix="af")
        docs = render_agent_docs(ctx)
        assert "af/task-" in docs["protocol.md"]

    def test_protocol_contains_qa_gates(self):
        ctx = _make_ctx()
        docs = render_agent_docs(ctx)
        assert "structural_check" in docs["protocol.md"]
        assert "command_check" in docs["protocol.md"]

    def test_protocol_contracts_when_complex(self):
        ctx = _make_ctx(coordination_complexity=2)
        docs = render_agent_docs(ctx)
        assert ".orchestrator/contracts.md" in docs["protocol.md"]

    def test_protocol_no_contracts_when_simple(self):
        ctx = _make_ctx(coordination_complexity=1)
        docs = render_agent_docs(ctx)
        assert ".orchestrator/contracts.md" not in docs["protocol.md"]

    def test_conventions_python(self):
        mod = _make_module(
            tech_stack=["Python", "FastAPI"],
            dependencies={"fastapi": ">=0.100", "pydantic": ">=2.0"},
        )
        ctx = _make_ctx(modules=[mod])
        docs = render_agent_docs(ctx)
        assert "type hints" in docs["conventions.md"]
        assert "Pydantic" in docs["conventions.md"]

    def test_conventions_typescript(self):
        mod = _make_module(
            name="frontend",
            path="frontend",
            tech_stack=["TypeScript", "React"],
            dependencies={"typescript": ">=5.0", "react": ">=18"},
        )
        ctx = _make_ctx(modules=[mod])
        docs = render_agent_docs(ctx)
        assert "strict TypeScript" in docs["conventions.md"]
        assert "functional components" in docs["conventions.md"]

    def test_boundaries_module_isolation(self):
        be = _make_module(name="backend", path="backend")
        fe = _make_module(name="frontend", path="frontend")
        ctx = _make_ctx(modules=[be, fe])
        docs = render_agent_docs(ctx)
        assert "backend" in docs["boundaries.md"]
        assert "frontend" in docs["boundaries.md"]
        assert "does NOT import" in docs["boundaries.md"]

    def test_boundaries_sensitive_paths(self):
        ctx = _make_ctx(sensitive_paths=[".env", "secrets/prod.key"])
        docs = render_agent_docs(ctx)
        assert ".env" in docs["boundaries.md"]
        assert "secrets/prod.key" in docs["boundaries.md"]

    def test_boundaries_cross_deps(self):
        dep = CrossModuleDep(
            from_module="frontend",
            to_module="backend",
            interface_type="api",
            description="REST calls",
        )
        be = _make_module(name="backend", path="backend")
        fe = _make_module(name="frontend", path="frontend")
        ctx = _make_ctx(modules=[be, fe], cross_deps=[dep])
        docs = render_agent_docs(ctx)
        assert "Allowed Interfaces" in docs["boundaries.md"]
        assert "REST calls" in docs["boundaries.md"]

    def test_boundaries_exceptions_section(self):
        ctx = _make_ctx()
        docs = render_agent_docs(ctx)
        assert "Exceptions" in docs["boundaries.md"]
