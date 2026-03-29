"""Tests for the clear command.

Tests removal of .orchestrator/ directory and legacy files, --force flag,
.gitignore cleanup, and 'no files found' case.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from typer.testing import CliRunner

from lindy_orchestrator.cli import app

runner = CliRunner()


def _setup_orchestrated_project(tmp_path: Path) -> Path:
    """Create a project with .orchestrator/ dir and all generated files."""
    orch = tmp_path / ".orchestrator"
    orch.mkdir(parents=True, exist_ok=True)

    # Config
    config = {
        "project": {"name": "test-project", "branch_prefix": "af"},
        "modules": [
            {"name": "backend", "path": "backend/"},
            {"name": "frontend", "path": "frontend/"},
        ],
    }
    (orch / "config.yaml").write_text(yaml.dump(config))

    # Claude files
    (orch / "claude").mkdir(parents=True, exist_ok=True)
    (orch / "claude" / "root.md").write_text("# Root CLAUDE.md\n")
    (orch / "claude" / "backend.md").write_text("# Backend CLAUDE.md\n")
    (orch / "claude" / "frontend.md").write_text("# Frontend CLAUDE.md\n")

    # Status files
    (orch / "status").mkdir(parents=True, exist_ok=True)
    (orch / "status" / "backend.md").write_text("# Backend Status\n")
    (orch / "status" / "frontend.md").write_text("# Frontend Status\n")

    # Architecture and contracts
    (orch / "architecture.md").write_text("# Architecture\n")
    (orch / "contracts.md").write_text("# Contracts\n")

    # Docs
    (orch / "docs").mkdir(parents=True, exist_ok=True)
    (orch / "docs" / "protocol.md").write_text("# Protocol\n")
    (orch / "docs" / "conventions.md").write_text("# Conventions\n")
    (orch / "docs" / "boundaries.md").write_text("# Boundaries\n")

    # Internal dirs
    (orch / "logs").mkdir(parents=True, exist_ok=True)
    (orch / "sessions").mkdir(parents=True, exist_ok=True)

    # Module dirs
    (tmp_path / "backend").mkdir(exist_ok=True)
    (tmp_path / "frontend").mkdir(exist_ok=True)

    # .gitignore with orchestrator entries
    (tmp_path / ".gitignore").write_text(
        "node_modules/\n.orchestrator/logs/\n.orchestrator/sessions/\n*.pyc\n"
    )

    return tmp_path


def _setup_legacy_project(tmp_path: Path) -> Path:
    """Create a project with legacy file layout (pre-.orchestrator/)."""
    # Legacy root files
    (tmp_path / "orchestrator.yaml").write_text("project:\n  name: test\n")
    (tmp_path / "ARCHITECTURE.md").write_text("# Architecture\n")
    (tmp_path / "CONTRACTS.md").write_text("# Contracts\n")
    # Root CLAUDE.md with orchestrator marker
    (tmp_path / "CLAUDE.md").write_text(
        "# lindy-orchestrator — Project Orchestrator\n\n> You coordinate modules.\n"
    )

    # Legacy docs
    agents = tmp_path / "docs" / "agents"
    agents.mkdir(parents=True, exist_ok=True)
    (agents / "protocol.md").write_text("# Protocol\n")
    (agents / "conventions.md").write_text("# Conventions\n")
    (agents / "boundaries.md").write_text("# Boundaries\n")

    # Legacy per-module files
    for mod in ("backend", "frontend"):
        mod_dir = tmp_path / mod
        mod_dir.mkdir(exist_ok=True)
        (mod_dir / "STATUS.md").write_text(f"# {mod} Status\n")
        (mod_dir / "CLAUDE.md").write_text(f"# {mod.title()} Agent\n\nPython module.\n")

    return tmp_path


class TestClearCommand:
    def test_clear_removes_orchestrator_dir(self, tmp_path, monkeypatch):
        """Clear removes the .orchestrator/ directory."""
        project = _setup_orchestrated_project(tmp_path)
        monkeypatch.chdir(project)

        result = runner.invoke(app, ["clear", "--force"])
        assert result.exit_code == 0
        assert not (project / ".orchestrator").exists()

    def test_clear_removes_legacy_files(self, tmp_path, monkeypatch):
        """Clear also removes legacy root files."""
        project = _setup_legacy_project(tmp_path)
        monkeypatch.chdir(project)

        result = runner.invoke(app, ["clear", "--force"])
        assert result.exit_code == 0
        assert not (project / "orchestrator.yaml").exists()
        assert not (project / "ARCHITECTURE.md").exists()
        assert not (project / "CONTRACTS.md").exists()

    def test_clear_removes_legacy_docs(self, tmp_path, monkeypatch):
        """Clear removes docs/agents/ directory."""
        project = _setup_legacy_project(tmp_path)
        monkeypatch.chdir(project)

        result = runner.invoke(app, ["clear", "--force"])
        assert result.exit_code == 0
        assert not (project / "docs" / "agents").exists()

    def test_clear_removes_legacy_module_files(self, tmp_path, monkeypatch):
        """Clear removes per-module STATUS.md and CLAUDE.md."""
        project = _setup_legacy_project(tmp_path)
        monkeypatch.chdir(project)

        result = runner.invoke(app, ["clear", "--force"])
        assert result.exit_code == 0
        assert not (project / "backend" / "STATUS.md").exists()
        assert not (project / "backend" / "CLAUDE.md").exists()
        assert not (project / "frontend" / "STATUS.md").exists()
        assert not (project / "frontend" / "CLAUDE.md").exists()

    def test_clear_with_both_layouts(self, tmp_path, monkeypatch):
        """Clear handles both .orchestrator/ and legacy files at once."""
        project = _setup_orchestrated_project(tmp_path)
        # Also add some legacy files
        (project / "orchestrator.yaml").write_text("legacy config")
        (project / "ARCHITECTURE.md").write_text("legacy arch")
        monkeypatch.chdir(project)

        result = runner.invoke(app, ["clear", "--force"])
        assert result.exit_code == 0
        assert not (project / ".orchestrator").exists()
        assert not (project / "orchestrator.yaml").exists()
        assert not (project / "ARCHITECTURE.md").exists()


class TestClearForceFlag:
    def test_clear_without_force_prompts(self, tmp_path, monkeypatch):
        """Without --force, clear should prompt for confirmation."""
        project = _setup_orchestrated_project(tmp_path)
        monkeypatch.chdir(project)

        # Simulate user saying "n" to confirmation prompt
        runner.invoke(app, ["clear"], input="n\n")
        # Files should still exist since we declined
        assert (project / ".orchestrator").exists()

    def test_clear_force_skips_prompt(self, tmp_path, monkeypatch):
        """--force flag skips the confirmation prompt."""
        project = _setup_orchestrated_project(tmp_path)
        monkeypatch.chdir(project)

        result = runner.invoke(app, ["clear", "--force"])
        assert result.exit_code == 0
        assert not (project / ".orchestrator").exists()

    def test_clear_force_short_flag(self, tmp_path, monkeypatch):
        """-f flag is shorthand for --force."""
        project = _setup_orchestrated_project(tmp_path)
        monkeypatch.chdir(project)

        result = runner.invoke(app, ["clear", "-f"])
        assert result.exit_code == 0
        assert not (project / ".orchestrator").exists()


class TestClearGitignore:
    def test_clear_cleans_gitignore(self, tmp_path, monkeypatch):
        """Clear removes orchestrator entries from .gitignore."""
        project = _setup_orchestrated_project(tmp_path)
        monkeypatch.chdir(project)

        result = runner.invoke(app, ["clear", "--force"])
        assert result.exit_code == 0

        gitignore = (project / ".gitignore").read_text()
        assert ".orchestrator/logs/" not in gitignore
        assert ".orchestrator/sessions/" not in gitignore
        # Non-orchestrator entries should be preserved
        assert "node_modules/" in gitignore
        assert "*.pyc" in gitignore

    def test_clear_no_gitignore(self, tmp_path, monkeypatch):
        """Clear handles missing .gitignore gracefully."""
        project = _setup_orchestrated_project(tmp_path)
        (project / ".gitignore").unlink()
        monkeypatch.chdir(project)

        result = runner.invoke(app, ["clear", "--force"])
        assert result.exit_code == 0


class TestClearNoFiles:
    def test_clear_empty_project(self, tmp_path, monkeypatch):
        """Clear on a project with no orchestrator files reports nothing found."""
        monkeypatch.chdir(tmp_path)

        result = runner.invoke(app, ["clear", "--force"])
        assert result.exit_code == 0
        assert "no" in result.output.lower() or "nothing" in result.output.lower()

    def test_clear_preserves_non_orchestrator_files(self, tmp_path, monkeypatch):
        """Clear does not remove non-orchestrator files."""
        project = _setup_orchestrated_project(tmp_path)
        (project / "README.md").write_text("# My Project\n")
        (project / "backend" / "main.py").write_text("print('hello')\n")
        monkeypatch.chdir(project)

        result = runner.invoke(app, ["clear", "--force"])
        assert result.exit_code == 0
        assert (project / "README.md").exists()
        assert (project / "backend" / "main.py").exists()
