"""Tests for the scaffold/onboard CLI command."""

from __future__ import annotations

import json
from unittest.mock import patch

from typer.testing import CliRunner

from lindy_orchestrator.cli import app
from lindy_orchestrator.cli_onboard import (
    _build_scaffold_prompt,
    parse_scaffold_response,
    scaffold_response_to_context,
)
from lindy_orchestrator.models import DispatchResult

runner = CliRunner()


# ---------------------------------------------------------------------------
# Sample LLM response fixture
# ---------------------------------------------------------------------------

SAMPLE_LLM_RESPONSE = {
    "project_name": "my-saas-app",
    "project_description": "A SaaS application with React frontend and Python backend",
    "modules": [
        {
            "name": "backend",
            "path": "backend",
            "tech_stack": ["Python", "FastAPI", "SQLAlchemy"],
            "detected_patterns": ["REST API", "database ORM"],
            "test_commands": ["pytest"],
            "build_commands": ["pip install -e ."],
            "lint_commands": ["ruff check ."],
        },
        {
            "name": "frontend",
            "path": "frontend",
            "tech_stack": ["TypeScript", "React", "Vite"],
            "detected_patterns": ["frontend SPA"],
            "test_commands": ["npm test"],
            "build_commands": ["npm run build"],
            "lint_commands": ["npm run lint"],
        },
    ],
    "cross_deps": [
        {
            "from_module": "frontend",
            "to_module": "backend",
            "interface_type": "api",
            "description": "REST API calls",
        }
    ],
    "coordination_complexity": 2,
    "branch_prefix": "af",
    "sensitive_paths": [".env", "*.key"],
    "qa_requirements": {
        "backend": ["pytest", "ruff check ."],
        "frontend": ["npm test", "npm run lint"],
    },
    "monorepo": True,
}


# ---------------------------------------------------------------------------
# Prompt construction tests
# ---------------------------------------------------------------------------


class TestPromptConstruction:
    def test_build_scaffold_prompt_includes_description(self):
        prompt = _build_scaffold_prompt("A todo app with React and Node.js")
        assert "A todo app with React and Node.js" in prompt

    def test_build_scaffold_prompt_includes_json_schema(self):
        prompt = _build_scaffold_prompt("Any project")
        assert "project_name" in prompt
        assert "modules" in prompt
        assert "cross_deps" in prompt
        assert "coordination_complexity" in prompt
        assert "qa_requirements" in prompt

    def test_build_scaffold_prompt_includes_instructions(self):
        prompt = _build_scaffold_prompt("Any project")
        assert "tech_stack" in prompt
        assert "interface_type" in prompt


# ---------------------------------------------------------------------------
# Response parsing tests
# ---------------------------------------------------------------------------


class TestResponseParsing:
    def test_parse_plain_json(self):
        raw = json.dumps(SAMPLE_LLM_RESPONSE)
        data = parse_scaffold_response(raw)
        assert data["project_name"] == "my-saas-app"
        assert len(data["modules"]) == 2

    def test_parse_json_with_markdown_fences(self):
        raw = f"```json\n{json.dumps(SAMPLE_LLM_RESPONSE)}\n```"
        data = parse_scaffold_response(raw)
        assert data["project_name"] == "my-saas-app"

    def test_parse_json_with_surrounding_text(self):
        raw = f"Here is the scaffold:\n{json.dumps(SAMPLE_LLM_RESPONSE)}\nDone."
        data = parse_scaffold_response(raw)
        assert data["project_name"] == "my-saas-app"

    def test_parse_invalid_json_raises(self):
        import pytest

        with pytest.raises((json.JSONDecodeError, ValueError)):
            parse_scaffold_response("this is not json at all")

    def test_parse_json_with_bare_code_fence(self):
        raw = f"```\n{json.dumps(SAMPLE_LLM_RESPONSE)}\n```"
        data = parse_scaffold_response(raw)
        assert data["project_name"] == "my-saas-app"

    def test_parse_empty_string_raises(self):
        import pytest

        with pytest.raises((json.JSONDecodeError, ValueError)):
            parse_scaffold_response("")


# ---------------------------------------------------------------------------
# DiscoveryContext conversion tests
# ---------------------------------------------------------------------------


class TestScaffoldResponseToContext:
    def test_basic_conversion(self):
        ctx = scaffold_response_to_context(SAMPLE_LLM_RESPONSE)
        assert ctx.project_name == "my-saas-app"
        assert len(ctx.modules) == 2
        assert ctx.modules[0].name == "backend"
        assert ctx.modules[1].name == "frontend"

    def test_module_tech_stacks(self):
        ctx = scaffold_response_to_context(SAMPLE_LLM_RESPONSE)
        assert "Python" in ctx.modules[0].tech_stack
        assert "React" in ctx.modules[1].tech_stack

    def test_cross_deps(self):
        ctx = scaffold_response_to_context(SAMPLE_LLM_RESPONSE)
        assert len(ctx.cross_deps) == 1
        assert ctx.cross_deps[0].from_module == "frontend"
        assert ctx.cross_deps[0].to_module == "backend"

    def test_coordination_complexity(self):
        ctx = scaffold_response_to_context(SAMPLE_LLM_RESPONSE)
        assert ctx.coordination_complexity == 2

    def test_qa_requirements(self):
        ctx = scaffold_response_to_context(SAMPLE_LLM_RESPONSE)
        assert "backend" in ctx.qa_requirements
        assert "pytest" in ctx.qa_requirements["backend"]

    def test_monorepo_flag(self):
        ctx = scaffold_response_to_context(SAMPLE_LLM_RESPONSE)
        assert ctx.monorepo is True

    def test_defaults_for_missing_fields(self):
        minimal = {"project_name": "test", "modules": [{"name": "app", "path": "app"}]}
        ctx = scaffold_response_to_context(minimal)
        assert ctx.project_name == "test"
        assert ctx.branch_prefix == "af"
        assert ctx.coordination_complexity == 1
        assert ctx.monorepo is False
        assert len(ctx.modules) == 1

    def test_output_dir_passthrough(self):
        ctx = scaffold_response_to_context(SAMPLE_LLM_RESPONSE, output_dir="/tmp/proj")
        assert ctx.root == "/tmp/proj"

    def test_empty_modules_list(self):
        data = {"project_name": "empty", "modules": []}
        ctx = scaffold_response_to_context(data)
        assert ctx.project_name == "empty"
        assert len(ctx.modules) == 0
        assert len(ctx.cross_deps) == 0

    def test_module_path_defaults_to_name(self):
        data = {"modules": [{"name": "svc"}]}
        ctx = scaffold_response_to_context(data)
        assert ctx.modules[0].path == "svc"

    def test_sensitive_paths(self):
        ctx = scaffold_response_to_context(SAMPLE_LLM_RESPONSE)
        assert ".env" in ctx.sensitive_paths
        assert "*.key" in ctx.sensitive_paths


# ---------------------------------------------------------------------------
# CLI integration tests (mocked LLM) — now using "onboard" command
# ---------------------------------------------------------------------------


def _mock_dispatch_simple(module, working_dir, prompt):
    """Mock the provider.dispatch_simple to return a sample LLM response."""
    return DispatchResult(
        module=module,
        success=True,
        output=json.dumps(SAMPLE_LLM_RESPONSE),
    )


class TestOnboardScaffoldCLI:
    """Tests for the scaffold mode of the unified onboard command."""

    def test_onboard_empty_project_no_description_exits(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, ["onboard", "-y"])
        # Empty project with no description should fail
        assert result.exit_code != 0

    def test_onboard_scaffold_no_cli(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        with patch("shutil.which", return_value=None):
            result = runner.invoke(app, ["onboard", "A test project", "-y"])
            assert result.exit_code != 0
            assert "not found" in result.output.lower()

    def test_onboard_scaffold_generates_files(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        with (
            patch("shutil.which", return_value="/usr/bin/claude"),
            patch("lindy_orchestrator.cli_onboard.create_provider") as mock_provider_factory,
        ):
            mock_provider = mock_provider_factory.return_value
            mock_provider.dispatch_simple.side_effect = _mock_dispatch_simple

            result = runner.invoke(
                app,
                [
                    "onboard",
                    "A SaaS app with React and Python",
                    "-y",
                ],
            )
            assert result.exit_code == 0
            assert "Onboarding complete" in result.output

            # Key files should exist under .orchestrator/
            assert (tmp_path / ".orchestrator" / "config.yaml").exists()
            assert (tmp_path / ".orchestrator" / "claude" / "root.md").exists()
            assert (tmp_path / ".orchestrator" / "architecture.md").exists()

            # Module status files under .orchestrator/status/
            assert (tmp_path / ".orchestrator" / "status" / "backend.md").exists()
            assert (tmp_path / ".orchestrator" / "status" / "frontend.md").exists()

            # contracts.md should exist (complexity >= 2)
            assert (tmp_path / ".orchestrator" / "contracts.md").exists()

    def test_onboard_scaffold_from_file(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        desc_file = tmp_path / "desc.md"
        desc_file.write_text("A microservice project with Go and gRPC")

        with (
            patch("shutil.which", return_value="/usr/bin/claude"),
            patch("lindy_orchestrator.cli_onboard.create_provider") as mock_provider_factory,
        ):
            mock_provider = mock_provider_factory.return_value
            mock_provider.dispatch_simple.side_effect = _mock_dispatch_simple

            result = runner.invoke(
                app,
                [
                    "onboard",
                    "--file",
                    str(desc_file),
                    "-y",
                ],
            )
            assert result.exit_code == 0
            assert "Onboarding complete" in result.output

    def test_onboard_scaffold_llm_failure(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

        def _mock_fail(module, working_dir, prompt):
            return DispatchResult(module=module, success=False, output="Connection error")

        with (
            patch("shutil.which", return_value="/usr/bin/claude"),
            patch("lindy_orchestrator.cli_onboard.create_provider") as mock_provider_factory,
        ):
            mock_provider = mock_provider_factory.return_value
            mock_provider.dispatch_simple.side_effect = _mock_fail

            result = runner.invoke(
                app,
                [
                    "onboard",
                    "A project",
                    "-y",
                ],
            )
            assert result.exit_code != 0
            assert "failed" in result.output.lower()

    def test_onboard_scaffold_invalid_json_response(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

        def _mock_bad_json(module, working_dir, prompt):
            return DispatchResult(module=module, success=True, output="Not valid JSON at all")

        with (
            patch("shutil.which", return_value="/usr/bin/claude"),
            patch("lindy_orchestrator.cli_onboard.create_provider") as mock_provider_factory,
        ):
            mock_provider = mock_provider_factory.return_value
            mock_provider.dispatch_simple.side_effect = _mock_bad_json

            result = runner.invoke(
                app,
                [
                    "onboard",
                    "A project",
                    "-y",
                ],
            )
            assert result.exit_code != 0
            assert "parse" in result.output.lower()

    def test_onboard_scaffold_force_overwrites(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        # With orchestrator.yaml present, it enters re-onboard mode.
        # Test force in scaffold mode: empty project, no config
        (tmp_path / "README.md").write_text("just a readme")

        with (
            patch("shutil.which", return_value="/usr/bin/claude"),
            patch("lindy_orchestrator.cli_onboard.create_provider") as mock_provider_factory,
        ):
            mock_provider = mock_provider_factory.return_value
            mock_provider.dispatch_simple.side_effect = _mock_dispatch_simple

            result = runner.invoke(
                app,
                [
                    "onboard",
                    "A project",
                    "--force",
                    "-y",
                ],
            )
            assert result.exit_code == 0
            # Files should be created
            assert (tmp_path / ".orchestrator" / "config.yaml").exists()

    def test_onboard_scaffold_with_codex_provider(self, tmp_path, monkeypatch):
        """--provider codex_cli should use codex provider."""
        monkeypatch.chdir(tmp_path)
        with (
            patch("shutil.which", return_value="/usr/bin/codex"),
            patch("lindy_orchestrator.cli_onboard.create_provider") as mock_provider_factory,
        ):
            mock_provider = mock_provider_factory.return_value
            mock_provider.dispatch_simple.side_effect = _mock_dispatch_simple

            result = runner.invoke(
                app,
                ["onboard", "A test project", "--provider", "codex_cli", "-y"],
            )
            assert result.exit_code == 0
            assert "Onboarding complete" in result.output


class TestOnboardInitCLI:
    """Tests for the init+onboard mode of the unified onboard command."""

    def test_onboard_detects_existing_project(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        # Create a module directory with a marker
        backend = tmp_path / "backend"
        backend.mkdir()
        (backend / "pyproject.toml").write_text('[project]\nname = "test"')

        result = runner.invoke(app, ["onboard", "-y"])
        # Should detect init+onboard mode
        assert "init+onboard" in result.output.lower() or result.exit_code == 0


class TestOldCommandsRemoved:
    """Verify that the old init and scaffold commands are no longer registered."""

    def test_init_command_not_found(self):
        result = runner.invoke(app, ["init"])
        assert result.exit_code != 0

    def test_scaffold_command_not_found(self):
        result = runner.invoke(app, ["scaffold"])
        assert result.exit_code != 0

    def test_onboard_command_exists(self):
        result = runner.invoke(app, ["onboard", "--help"])
        assert result.exit_code == 0
        assert "onboard" in result.output.lower()

    def test_onboard_has_provider_option(self):
        result = runner.invoke(app, ["onboard", "--help"])
        # Strip ANSI escape codes before checking
        import re

        clean = re.sub(r"\x1b\[[0-9;]*m", "", result.output)
        assert "--provider" in clean
