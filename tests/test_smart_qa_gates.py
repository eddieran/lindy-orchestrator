"""Tests for smart QA gate classification in onboard config generation."""

from __future__ import annotations

from lindy_orchestrator.discovery.generator import _build_qa_gates, _classify_gate, _is_lint_command
from lindy_orchestrator.models import DiscoveryContext


class TestClassifyGate:
    def test_go_test_splits_fast_and_full(self) -> None:
        gates = _classify_gate("api", "go test ./...")
        assert len(gates) == 2
        fast = gates[0]
        full = gates[1]
        assert fast["name"] == "api-test-fast"
        assert "-short" in fast["command"]
        assert fast.get("required") is None  # required by default
        assert full["name"] == "api-test-full"
        assert full["required"] == "false"
        assert full["timeout"] == "600"

    def test_pytest_adds_fast_flags(self) -> None:
        gates = _classify_gate("backend", "pytest")
        assert len(gates) == 1
        assert "-x -q --tb=short" in gates[0]["command"]

    def test_playwright_marked_optional(self) -> None:
        gates = _classify_gate("frontend", "npx playwright test")
        assert len(gates) == 1
        assert gates[0]["required"] == "false"
        assert gates[0]["timeout"] == "600"

    def test_eslint_gets_diff_only(self) -> None:
        gates = _classify_gate("web", "npm run lint")
        assert len(gates) == 1
        assert gates[0]["diff_only"] == "true"

    def test_ruff_check_gets_diff_only(self) -> None:
        gates = _classify_gate("api", "ruff check .")
        assert len(gates) == 1
        assert gates[0]["diff_only"] == "true"

    def test_cargo_test_not_split(self) -> None:
        gates = _classify_gate("core", "cargo test")
        assert len(gates) == 1
        assert "required" not in gates[0]

    def test_generic_command_passes_through(self) -> None:
        gates = _classify_gate("mod", "make check")
        assert len(gates) == 1
        assert gates[0]["command"] == "make check"
        assert "required" not in gates[0]
        assert "diff_only" not in gates[0]

    def test_e2e_keyword_marked_optional(self) -> None:
        gates = _classify_gate("app", "npm run e2e")
        assert len(gates) == 1
        assert gates[0]["required"] == "false"

    def test_cypress_marked_optional(self) -> None:
        gates = _classify_gate("web", "npx cypress run")
        assert len(gates) == 1
        assert gates[0]["required"] == "false"


class TestIsLintCommand:
    def test_eslint(self) -> None:
        assert _is_lint_command("eslint .") is True

    def test_ruff_check(self) -> None:
        assert _is_lint_command("ruff check src/") is True

    def test_mypy(self) -> None:
        assert _is_lint_command("mypy .") is True

    def test_go_vet(self) -> None:
        assert _is_lint_command("go vet ./...") is True

    def test_npm_run_lint(self) -> None:
        assert _is_lint_command("npm run lint") is True

    def test_pytest_not_lint(self) -> None:
        assert _is_lint_command("pytest") is False

    def test_go_test_not_lint(self) -> None:
        assert _is_lint_command("go test ./...") is False


class TestBuildQaGates:
    def test_go_project_generates_fast_and_full(self) -> None:
        ctx = DiscoveryContext(
            project_name="myapp",
            project_description="test",
            root="/tmp",
            qa_requirements={"api": ["go test ./...", "go vet ./..."]},
        )
        gates = _build_qa_gates(ctx)
        names = [g["name"] for g in gates]
        assert "api-test-fast" in names
        assert "api-test-full" in names
        # go vet slugifies to "go" — verify it's present and has diff_only
        vet_gate = next(g for g in gates if "go vet" in g["command"])
        assert vet_gate.get("diff_only") == "true"

    def test_node_project_lint_diff_only(self) -> None:
        ctx = DiscoveryContext(
            project_name="web",
            project_description="test",
            root="/tmp",
            qa_requirements={"frontend": ["npm run lint", "npx playwright test"]},
        )
        gates = _build_qa_gates(ctx)
        lint_gate = next(g for g in gates if "lint" in g["name"])
        assert lint_gate.get("diff_only") == "true"
        playwright_gate = next(g for g in gates if "playwright" in g["name"])
        assert playwright_gate["required"] == "false"

    def test_python_project_pytest_fast(self) -> None:
        ctx = DiscoveryContext(
            project_name="api",
            project_description="test",
            root="/tmp",
            qa_requirements={"backend": ["pytest", "ruff check ."]},
        )
        gates = _build_qa_gates(ctx)
        pytest_gate = next(g for g in gates if "pytest" in g["name"])
        assert "-x -q --tb=short" in pytest_gate["command"]
        ruff_gate = next(g for g in gates if "ruff" in g["name"])
        assert ruff_gate.get("diff_only") == "true"


class TestConfigRendering:
    def test_rendered_config_includes_required_false(self) -> None:
        from lindy_orchestrator.discovery.generator import _render_config
        from lindy_orchestrator.models import ModuleProfile

        ctx = DiscoveryContext(
            project_name="test",
            project_description="test",
            root="/tmp",
            modules=[ModuleProfile(name="api", path="api")],
            qa_requirements={"api": ["go test ./..."]},
        )
        config = _render_config(ctx)
        assert "required: false" in config
        assert "test-fast" in config
        assert "test-full" in config

    def test_rendered_config_includes_diff_only(self) -> None:
        from lindy_orchestrator.discovery.generator import _render_config
        from lindy_orchestrator.models import ModuleProfile

        ctx = DiscoveryContext(
            project_name="test",
            project_description="test",
            root="/tmp",
            modules=[ModuleProfile(name="web", path="web")],
            qa_requirements={"web": ["npm run lint"]},
        )
        config = _render_config(ctx)
        assert "diff_only: true" in config

    def test_rendered_config_includes_timeout(self) -> None:
        from lindy_orchestrator.discovery.generator import _render_config
        from lindy_orchestrator.models import ModuleProfile

        ctx = DiscoveryContext(
            project_name="test",
            project_description="test",
            root="/tmp",
            modules=[ModuleProfile(name="api", path="api")],
            qa_requirements={"api": ["go test ./..."]},
        )
        config = _render_config(ctx)
        assert "timeout: 600" in config
