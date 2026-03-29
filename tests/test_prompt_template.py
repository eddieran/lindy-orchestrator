"""Tests for prompt template system (gather_* functions and build_prompt)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import yaml

from lindy_orchestrator.config import OrchestratorConfig, load_config
from lindy_orchestrator.models import TaskItem
from lindy_orchestrator.task_preparation import (
    build_prompt,
    gather_branch_delivery,
    gather_claude_md,
    gather_mailbox_messages,
    gather_status_content,
)


def _make_task(module: str = "backend", prompt: str = "Do the thing") -> TaskItem:
    return TaskItem(id=1, module=module, description="test", prompt=prompt)


def _make_config(tmp_path: Path, extras: dict | None = None) -> OrchestratorConfig:
    data = {
        "project": {"name": "test"},
        "modules": [{"name": "backend", "path": "backend/"}],
        "mailbox": {"enabled": False},
    }
    if extras:
        data.update(extras)
    cfg_path = tmp_path / "orchestrator.yaml"
    cfg_path.write_text(yaml.dump(data))
    return load_config(cfg_path)


class TestGatherFunctions:
    def test_gather_status_content_returns_string(self, tmp_path: Path) -> None:
        cfg = _make_config(tmp_path)
        status_dir = tmp_path / ".orchestrator" / "status"
        status_dir.mkdir(parents=True, exist_ok=True)
        (status_dir / "backend.md").write_text("All green")
        task = _make_task()

        result = gather_status_content(task, cfg)
        assert "## Current STATUS.md" in result
        assert "All green" in result

    def test_gather_status_content_missing_file(self, tmp_path: Path) -> None:
        cfg = _make_config(tmp_path)
        task = _make_task()
        assert gather_status_content(task, cfg) == ""

    def test_gather_claude_md_returns_string(self, tmp_path: Path) -> None:
        cfg = _make_config(tmp_path)
        claude_dir = tmp_path / ".orchestrator" / "claude"
        claude_dir.mkdir(parents=True, exist_ok=True)
        (claude_dir / "root.md").write_text("Root instructions")
        task = _make_task()

        result = gather_claude_md(task, cfg)
        assert "## CLAUDE.md Instructions" in result
        assert "Root instructions" in result

    def test_gather_mailbox_messages_disabled(self, tmp_path: Path) -> None:
        cfg = _make_config(tmp_path)
        task = _make_task()
        assert gather_mailbox_messages(task, cfg) == ""

    def test_gather_branch_delivery_first_dispatch(self) -> None:
        task = _make_task()
        result = gather_branch_delivery(task, "af/task-1", None, 0)
        assert "Branch delivery" in result
        assert "af/task-1" in result

    def test_gather_branch_delivery_retry_returns_empty(self) -> None:
        task = _make_task()
        result = gather_branch_delivery(task, "af/task-1", None, 1)
        assert result == ""

    def test_gather_branch_delivery_worktree(self, tmp_path: Path) -> None:
        task = _make_task()
        result = gather_branch_delivery(task, "af/task-1", tmp_path, 0)
        assert "already on branch" in result


class TestBuildPrompt:
    def test_default_order_without_template(self, tmp_path: Path) -> None:
        cfg = _make_config(tmp_path)
        status_dir = tmp_path / ".orchestrator" / "status"
        status_dir.mkdir(parents=True, exist_ok=True)
        (status_dir / "backend.md").write_text("Status here")

        task = _make_task(prompt="User prompt")
        progress = MagicMock()

        result = build_prompt(task, cfg, "af/task-1", None, 0, progress)
        # Default order: status, claude_md, user_prompt, mailbox, branch
        assert "Status here" in result
        assert "User prompt" in result
        assert "af/task-1" in result
        # Status comes before user prompt
        assert result.index("Status here") < result.index("User prompt")

    def test_template_variable_substitution(self, tmp_path: Path) -> None:
        tpl_path = tmp_path / "template.txt"
        tpl_path.write_text(
            "INSTRUCTIONS:\n{claude_md}\n\nSTATUS:\n{status_content}\n\n"
            "TASK:\n{user_prompt}\n\nBRANCH:\n{branch_instructions}\n\n"
            "MAIL:\n{mailbox_messages}"
        )
        cfg = _make_config(tmp_path, {"dispatcher": {"prompt_template": str(tpl_path)}})

        task = _make_task(prompt="Build the feature")
        progress = MagicMock()

        result = build_prompt(task, cfg, "af/task-1", None, 0, progress)
        assert "TASK:" in result
        assert "Build the feature" in result
        assert "BRANCH:" in result

    def test_missing_template_file_fallback(self, tmp_path: Path) -> None:
        cfg = _make_config(tmp_path, {"dispatcher": {"prompt_template": "/nonexistent/tpl.txt"}})
        task = _make_task(prompt="Fallback prompt")
        progress = MagicMock()

        result = build_prompt(task, cfg, "af/task-1", None, 0, progress)
        # Falls back to default order
        assert "Fallback prompt" in result

    def test_bad_variable_fallback(self, tmp_path: Path) -> None:
        tpl_path = tmp_path / "bad_template.txt"
        tpl_path.write_text("Hello {undefined_var}")
        cfg = _make_config(tmp_path, {"dispatcher": {"prompt_template": str(tpl_path)}})

        task = _make_task(prompt="Safe prompt")
        progress = MagicMock()

        result = build_prompt(task, cfg, "af/task-1", None, 0, progress)
        # Falls back to default order
        assert "Safe prompt" in result

    def test_retry_skips_status_and_claude_md(self, tmp_path: Path) -> None:
        cfg = _make_config(tmp_path)
        status_dir = tmp_path / ".orchestrator" / "status"
        status_dir.mkdir(parents=True, exist_ok=True)
        (status_dir / "backend.md").write_text("Should not appear")

        claude_dir = tmp_path / ".orchestrator" / "claude"
        claude_dir.mkdir(parents=True, exist_ok=True)
        (claude_dir / "root.md").write_text("Also should not appear")

        task = _make_task(prompt="Retry prompt")
        progress = MagicMock()

        result = build_prompt(task, cfg, "af/task-1", None, 1, progress)
        assert "Should not appear" not in result
        assert "Also should not appear" not in result
        assert "Retry prompt" in result
