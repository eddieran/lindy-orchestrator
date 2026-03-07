"""Tests for the scaffold CLI command."""

from __future__ import annotations

import json
from unittest.mock import patch

from typer.testing import CliRunner

from lindy_orchestrator.cli import app
from lindy_orchestrator.cli_scaffold import (
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
# CLI integration tests (mocked LLM)
# ---------------------------------------------------------------------------


def _mock_dispatch_simple(module, working_dir, prompt):
    """Mock the provider.dispatch_simple to return a sample LLM response."""
    return DispatchResult(
        module=module,
        success=True,
        output=json.dumps(SAMPLE_LLM_RESPONSE),
    )


class TestScaffoldCLI:
    def test_scaffold_no_description_exits(self):
        result = runner.invoke(app, ["scaffold"])
        assert result.exit_code != 0

    def test_scaffold_no_claude_cli(self):
        with patch("lindy_orchestrator.cli_scaffold.find_claude_cli", return_value=None):
            result = runner.invoke(app, ["scaffold", "A test project"])
            assert result.exit_code != 0
            assert "Claude CLI not found" in result.output

    def test_scaffold_dry_run(self, tmp_path):
        with (
            patch(
                "lindy_orchestrator.cli_scaffold.find_claude_cli",
                return_value="/usr/bin/claude",
            ),
            patch("lindy_orchestrator.cli_scaffold.create_provider") as mock_provider_factory,
        ):
            mock_provider = mock_provider_factory.return_value
            mock_provider.dispatch_simple.side_effect = _mock_dispatch_simple

            result = runner.invoke(
                app,
                [
                    "scaffold",
                    "A SaaS app with React and Python",
                    "--output-dir",
                    str(tmp_path),
                    "--dry-run",
                ],
            )
            assert result.exit_code == 0
            assert "Dry run" in result.output
            assert "orchestrator.yaml" in result.output
            # No files should be written
            assert not (tmp_path / "orchestrator.yaml").exists()

    def test_scaffold_generates_files(self, tmp_path):
        with (
            patch(
                "lindy_orchestrator.cli_scaffold.find_claude_cli",
                return_value="/usr/bin/claude",
            ),
            patch("lindy_orchestrator.cli_scaffold.create_provider") as mock_provider_factory,
        ):
            mock_provider = mock_provider_factory.return_value
            mock_provider.dispatch_simple.side_effect = _mock_dispatch_simple

            result = runner.invoke(
                app,
                [
                    "scaffold",
                    "A SaaS app with React and Python",
                    "--output-dir",
                    str(tmp_path),
                    "-y",
                ],
            )
            assert result.exit_code == 0
            assert "Scaffold complete" in result.output

            # Key files should exist
            assert (tmp_path / "orchestrator.yaml").exists()
            assert (tmp_path / "CLAUDE.md").exists()
            assert (tmp_path / "ARCHITECTURE.md").exists()

            # Module dirs and STATUS.md
            assert (tmp_path / "backend" / "STATUS.md").exists()
            assert (tmp_path / "frontend" / "STATUS.md").exists()

            # CONTRACTS.md should exist (complexity >= 2)
            assert (tmp_path / "CONTRACTS.md").exists()

    def test_scaffold_from_file(self, tmp_path):
        desc_file = tmp_path / "desc.md"
        desc_file.write_text("A microservice project with Go and gRPC")

        with (
            patch(
                "lindy_orchestrator.cli_scaffold.find_claude_cli",
                return_value="/usr/bin/claude",
            ),
            patch("lindy_orchestrator.cli_scaffold.create_provider") as mock_provider_factory,
        ):
            mock_provider = mock_provider_factory.return_value
            mock_provider.dispatch_simple.side_effect = _mock_dispatch_simple

            result = runner.invoke(
                app,
                [
                    "scaffold",
                    "--file",
                    str(desc_file),
                    "--output-dir",
                    str(tmp_path / "output"),
                    "-y",
                ],
            )
            assert result.exit_code == 0
            assert "Scaffold complete" in result.output

    def test_scaffold_llm_failure(self, tmp_path):
        def _mock_fail(module, working_dir, prompt):
            return DispatchResult(module=module, success=False, output="Connection error")

        with (
            patch(
                "lindy_orchestrator.cli_scaffold.find_claude_cli",
                return_value="/usr/bin/claude",
            ),
            patch("lindy_orchestrator.cli_scaffold.create_provider") as mock_provider_factory,
        ):
            mock_provider = mock_provider_factory.return_value
            mock_provider.dispatch_simple.side_effect = _mock_fail

            result = runner.invoke(
                app,
                [
                    "scaffold",
                    "A project",
                    "--output-dir",
                    str(tmp_path),
                    "-y",
                ],
            )
            assert result.exit_code != 0
            assert "failed" in result.output.lower()

    def test_scaffold_invalid_json_response(self, tmp_path):
        def _mock_bad_json(module, working_dir, prompt):
            return DispatchResult(module=module, success=True, output="Not valid JSON at all")

        with (
            patch(
                "lindy_orchestrator.cli_scaffold.find_claude_cli",
                return_value="/usr/bin/claude",
            ),
            patch("lindy_orchestrator.cli_scaffold.create_provider") as mock_provider_factory,
        ):
            mock_provider = mock_provider_factory.return_value
            mock_provider.dispatch_simple.side_effect = _mock_bad_json

            result = runner.invoke(
                app,
                [
                    "scaffold",
                    "A project",
                    "--output-dir",
                    str(tmp_path),
                    "-y",
                ],
            )
            assert result.exit_code != 0
            assert "parse" in result.output.lower()

    def test_scaffold_force_overwrites(self, tmp_path):
        # Pre-create a file
        (tmp_path / "orchestrator.yaml").write_text("original")

        with (
            patch(
                "lindy_orchestrator.cli_scaffold.find_claude_cli",
                return_value="/usr/bin/claude",
            ),
            patch("lindy_orchestrator.cli_scaffold.create_provider") as mock_provider_factory,
        ):
            mock_provider = mock_provider_factory.return_value
            mock_provider.dispatch_simple.side_effect = _mock_dispatch_simple

            result = runner.invoke(
                app,
                [
                    "scaffold",
                    "A project",
                    "--output-dir",
                    str(tmp_path),
                    "--force",
                    "-y",
                ],
            )
            assert result.exit_code == 0
            # File should be overwritten
            content = (tmp_path / "orchestrator.yaml").read_text()
            assert content != "original"
            assert "my-saas-app" in content
