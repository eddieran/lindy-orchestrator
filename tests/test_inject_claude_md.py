"""Tests for inject_claude_md() and inject_status_content() functions.

These functions prepend CLAUDE.md instructions and STATUS.md content to task
prompts before dispatching to agents.
"""

from __future__ import annotations

from pathlib import Path

from lindy_orchestrator.config import ModuleConfig, OrchestratorConfig, ProjectConfig
from lindy_orchestrator.models import TaskItem, TaskStatus
from lindy_orchestrator.scheduler_helpers import inject_claude_md, inject_status_content


def _make_config(tmp_path: Path) -> OrchestratorConfig:
    """Create a test config rooted at tmp_path."""
    cfg = OrchestratorConfig(
        project=ProjectConfig(name="test-project", branch_prefix="af"),
        modules=[
            ModuleConfig(name="backend", path="backend"),
            ModuleConfig(name="frontend", path="frontend"),
        ],
    )
    cfg._config_dir = tmp_path
    return cfg


def _make_task(module: str = "backend", prompt: str = "Do the work") -> TaskItem:
    """Create a test task."""
    return TaskItem(
        id=1,
        module=module,
        description="Test task",
        prompt=prompt,
        status=TaskStatus.PENDING,
    )


def _noop_progress(msg: str) -> None:
    """No-op progress callback."""


# ---------------------------------------------------------------------------
# inject_claude_md
# ---------------------------------------------------------------------------


class TestInjectClaudeMd:
    def test_prepends_root_and_module_instructions(self, tmp_path: Path):
        """When both root.md and module.md exist, both are prepended."""
        cfg = _make_config(tmp_path)
        claude_dir = tmp_path / ".orchestrator" / "claude"
        claude_dir.mkdir(parents=True, exist_ok=True)
        (claude_dir / "root.md").write_text("Root instructions here")
        (claude_dir / "backend.md").write_text("Backend-specific instructions")

        task = _make_task(module="backend", prompt="Original prompt")
        inject_claude_md(task, cfg, _noop_progress)

        assert "Root instructions here" in task.prompt
        assert "Backend-specific instructions" in task.prompt
        assert "Original prompt" in task.prompt
        # Instructions should be prepended (before original prompt)
        root_pos = task.prompt.index("Root instructions here")
        original_pos = task.prompt.index("Original prompt")
        assert root_pos < original_pos

    def test_only_root_when_module_missing(self, tmp_path: Path):
        """When module.md doesn't exist, only root.md is injected."""
        cfg = _make_config(tmp_path)
        claude_dir = tmp_path / ".orchestrator" / "claude"
        claude_dir.mkdir(parents=True, exist_ok=True)
        (claude_dir / "root.md").write_text("Root only")

        task = _make_task(module="backend", prompt="Original")
        inject_claude_md(task, cfg, _noop_progress)

        assert "Root only" in task.prompt
        assert "Original" in task.prompt

    def test_only_module_when_root_missing(self, tmp_path: Path):
        """When root.md doesn't exist, only module.md is injected."""
        cfg = _make_config(tmp_path)
        claude_dir = tmp_path / ".orchestrator" / "claude"
        claude_dir.mkdir(parents=True, exist_ok=True)
        (claude_dir / "backend.md").write_text("Backend only")

        task = _make_task(module="backend", prompt="Original")
        inject_claude_md(task, cfg, _noop_progress)

        assert "Backend only" in task.prompt
        assert "Original" in task.prompt

    def test_no_files_present(self, tmp_path: Path):
        """When no claude files exist, prompt is unchanged."""
        cfg = _make_config(tmp_path)
        # Don't create any claude files

        task = _make_task(module="backend", prompt="Original prompt")
        inject_claude_md(task, cfg, _noop_progress)

        assert task.prompt == "Original prompt"

    def test_empty_claude_dir(self, tmp_path: Path):
        """When claude dir exists but is empty, prompt is unchanged."""
        cfg = _make_config(tmp_path)
        (tmp_path / ".orchestrator" / "claude").mkdir(parents=True, exist_ok=True)

        task = _make_task(module="backend", prompt="Original prompt")
        inject_claude_md(task, cfg, _noop_progress)

        assert task.prompt == "Original prompt"

    def test_read_error_handled_gracefully(self, tmp_path: Path):
        """Read errors should not crash — logged as warnings."""
        cfg = _make_config(tmp_path)
        claude_dir = tmp_path / ".orchestrator" / "claude"
        claude_dir.mkdir(parents=True, exist_ok=True)
        # Create a directory where a file is expected (causes read error)
        (claude_dir / "root.md").mkdir()

        task = _make_task(module="backend", prompt="Original")
        # Should not raise
        inject_claude_md(task, cfg, _noop_progress)
        assert "Original" in task.prompt

    def test_header_included(self, tmp_path: Path):
        """Injected content includes a header section marker."""
        cfg = _make_config(tmp_path)
        claude_dir = tmp_path / ".orchestrator" / "claude"
        claude_dir.mkdir(parents=True, exist_ok=True)
        (claude_dir / "root.md").write_text("Some instructions")

        task = _make_task(module="backend", prompt="Original")
        inject_claude_md(task, cfg, _noop_progress)

        assert "CLAUDE.md" in task.prompt or "Instructions" in task.prompt


# ---------------------------------------------------------------------------
# inject_status_content
# ---------------------------------------------------------------------------


class TestInjectStatusContent:
    def test_prepends_status_content(self, tmp_path: Path):
        """When status file exists, its content is prepended to prompt."""
        cfg = _make_config(tmp_path)
        status_dir = tmp_path / ".orchestrator" / "status"
        status_dir.mkdir(parents=True, exist_ok=True)
        (status_dir / "backend.md").write_text(
            "# Backend Status\n\n"
            "## Meta\n"
            "| Key | Value |\n"
            "| overall_health | GREEN |\n"
        )

        task = _make_task(module="backend", prompt="Original prompt")
        inject_status_content(task, cfg, _noop_progress)

        assert "Backend Status" in task.prompt or "GREEN" in task.prompt
        assert "Original prompt" in task.prompt
        # Status should be prepended
        status_pos = task.prompt.index("GREEN")
        original_pos = task.prompt.index("Original prompt")
        assert status_pos < original_pos

    def test_no_status_file(self, tmp_path: Path):
        """When status file doesn't exist, prompt is unchanged."""
        cfg = _make_config(tmp_path)

        task = _make_task(module="backend", prompt="Original prompt")
        inject_status_content(task, cfg, _noop_progress)

        assert task.prompt == "Original prompt"

    def test_status_dir_missing(self, tmp_path: Path):
        """When .orchestrator/status/ dir doesn't exist, prompt is unchanged."""
        cfg = _make_config(tmp_path)

        task = _make_task(module="backend", prompt="Original")
        inject_status_content(task, cfg, _noop_progress)

        assert task.prompt == "Original"

    def test_read_error_handled(self, tmp_path: Path):
        """Read errors should not crash."""
        cfg = _make_config(tmp_path)
        status_dir = tmp_path / ".orchestrator" / "status"
        status_dir.mkdir(parents=True, exist_ok=True)
        # Create a directory where a file is expected
        (status_dir / "backend.md").mkdir()

        task = _make_task(module="backend", prompt="Original")
        inject_status_content(task, cfg, _noop_progress)
        assert "Original" in task.prompt

    def test_header_marker_included(self, tmp_path: Path):
        """Injected status includes a section header."""
        cfg = _make_config(tmp_path)
        status_dir = tmp_path / ".orchestrator" / "status"
        status_dir.mkdir(parents=True, exist_ok=True)
        (status_dir / "backend.md").write_text("# Status\n| health | GREEN |\n")

        task = _make_task(module="backend", prompt="Do work")
        inject_status_content(task, cfg, _noop_progress)

        assert "STATUS" in task.prompt or "Status" in task.prompt

    def test_different_module(self, tmp_path: Path):
        """Inject reads the correct module's status file."""
        cfg = _make_config(tmp_path)
        status_dir = tmp_path / ".orchestrator" / "status"
        status_dir.mkdir(parents=True, exist_ok=True)
        (status_dir / "backend.md").write_text("Backend data")
        (status_dir / "frontend.md").write_text("Frontend data")

        task = _make_task(module="frontend", prompt="Work on UI")
        inject_status_content(task, cfg, _noop_progress)

        assert "Frontend data" in task.prompt
        assert "Backend data" not in task.prompt
