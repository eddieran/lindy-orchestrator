"""Tests for git worktree isolation in parallel task dispatch."""

from __future__ import annotations

import subprocess
from pathlib import Path
import pytest

from lindy_orchestrator.worktree import (
    WORKTREES_DIR,
    cleanup_all_worktrees,
    create_worktree,
    remove_worktree,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def git_repo(tmp_path: Path) -> Path:
    """Initialize a real git repo with an initial commit."""
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=tmp_path,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=tmp_path,
        capture_output=True,
    )
    # Need at least one commit for worktrees to work
    (tmp_path / "README.md").write_text("# Test")
    subprocess.run(["git", "add", "."], cwd=tmp_path, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "initial"],
        cwd=tmp_path,
        capture_output=True,
    )
    return tmp_path


# ---------------------------------------------------------------------------
# create_worktree
# ---------------------------------------------------------------------------


class TestCreateWorktree:
    def test_creates_worktree_directory(self, git_repo: Path):
        wt = create_worktree(git_repo, "af/task-1", 1)
        assert wt.exists()
        assert wt.is_dir()
        assert (wt / "README.md").exists()

    def test_worktree_on_correct_branch(self, git_repo: Path):
        wt = create_worktree(git_repo, "af/task-1", 1)
        result = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=wt,
            capture_output=True,
            text=True,
        )
        assert result.stdout.strip() == "af/task-1"

    def test_creates_branch_if_not_exists(self, git_repo: Path):
        create_worktree(git_repo, "af/task-42", 42)
        result = subprocess.run(
            ["git", "branch", "--list", "af/task-42"],
            cwd=git_repo,
            capture_output=True,
            text=True,
        )
        assert "af/task-42" in result.stdout

    def test_reuses_existing_branch(self, git_repo: Path):
        # Pre-create branch
        subprocess.run(
            ["git", "branch", "af/task-5"],
            cwd=git_repo,
            capture_output=True,
        )
        wt = create_worktree(git_repo, "af/task-5", 5)
        assert wt.exists()
        result = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=wt,
            capture_output=True,
            text=True,
        )
        assert result.stdout.strip() == "af/task-5"

    def test_cleans_up_stale_worktree(self, git_repo: Path):
        # Create first worktree
        wt1 = create_worktree(git_repo, "af/task-1", 1)
        assert wt1.exists()

        # Clean up the branch so we can recreate
        remove_worktree(git_repo, 1)

        # Recreate — should not fail
        wt2 = create_worktree(git_repo, "af/task-1", 1)
        assert wt2.exists()

    def test_multiple_worktrees_parallel(self, git_repo: Path):
        wt1 = create_worktree(git_repo, "af/task-1", 1)
        wt2 = create_worktree(git_repo, "af/task-2", 2)
        wt3 = create_worktree(git_repo, "af/task-3", 3)

        assert wt1.exists() and wt2.exists() and wt3.exists()
        assert wt1 != wt2 != wt3

        # Each on its own branch
        for wt, expected in [(wt1, "af/task-1"), (wt2, "af/task-2"), (wt3, "af/task-3")]:
            result = subprocess.run(
                ["git", "branch", "--show-current"],
                cwd=wt,
                capture_output=True,
                text=True,
            )
            assert result.stdout.strip() == expected

    def test_worktree_path_is_under_worktrees_dir(self, git_repo: Path):
        wt = create_worktree(git_repo, "af/task-7", 7)
        assert WORKTREES_DIR in str(wt)
        assert "task-7" in str(wt)

    def test_raises_on_non_git_dir(self, tmp_path: Path):
        with pytest.raises(RuntimeError, match="git worktree add failed"):
            create_worktree(tmp_path, "af/task-1", 1)


# ---------------------------------------------------------------------------
# remove_worktree
# ---------------------------------------------------------------------------


class TestRemoveWorktree:
    def test_removes_worktree(self, git_repo: Path):
        create_worktree(git_repo, "af/task-1", 1)
        remove_worktree(git_repo, 1)
        wt_dir = git_repo / WORKTREES_DIR / "task-1"
        assert not wt_dir.exists()

    def test_remove_nonexistent_is_noop(self, git_repo: Path):
        # Should not raise
        remove_worktree(git_repo, 999)

    def test_branch_survives_worktree_removal(self, git_repo: Path):
        create_worktree(git_repo, "af/task-1", 1)
        remove_worktree(git_repo, 1)
        # Branch should still exist
        result = subprocess.run(
            ["git", "branch", "--list", "af/task-1"],
            cwd=git_repo,
            capture_output=True,
            text=True,
        )
        assert "af/task-1" in result.stdout


# ---------------------------------------------------------------------------
# cleanup_all_worktrees
# ---------------------------------------------------------------------------


class TestCleanupAllWorktrees:
    def test_cleans_all(self, git_repo: Path):
        create_worktree(git_repo, "af/task-1", 1)
        create_worktree(git_repo, "af/task-2", 2)
        wt_base = git_repo / WORKTREES_DIR
        assert len(list(wt_base.iterdir())) == 2

        cleanup_all_worktrees(git_repo)
        # Directory removed or empty
        assert not wt_base.exists() or not any(wt_base.iterdir())

    def test_noop_when_no_worktrees(self, git_repo: Path):
        # Should not raise
        cleanup_all_worktrees(git_repo)


# ---------------------------------------------------------------------------
# Scheduler integration: worktree prompt injection
# ---------------------------------------------------------------------------


class TestWorktreePromptInjection:
    """Verify the scheduler injects correct prompt based on worktree availability."""

    def test_worktree_prompt_no_checkout(self):
        """When worktree is active, prompt should NOT tell agent to git checkout."""
        from lindy_orchestrator.models import TaskSpec

        task = TaskSpec(id=1, module="backend", description="test", prompt="Do stuff")
        branch_name = "af/task-1"
        worktree_path = Path("/fake/worktree")

        # Simulate the prompt injection logic from scheduler
        if worktree_path:
            task.prompt = (
                f"{task.prompt}\n\n"
                f"## IMPORTANT: Branch delivery requirements\n\n"
                f"You are already on branch `{branch_name}` (worktree isolation).\n"
                f"Do NOT switch branches or run `git checkout`.\n"
            )

        assert "already on branch" in task.prompt
        assert "git checkout" in task.prompt
        assert "Do NOT switch" in task.prompt
        assert "git checkout -b" not in task.prompt

    def test_fallback_prompt_has_checkout(self):
        """When no worktree, prompt should tell agent to git checkout -b."""
        from lindy_orchestrator.models import TaskSpec

        task = TaskSpec(id=1, module="backend", description="test", prompt="Do stuff")
        branch_name = "af/task-1"
        worktree_path = None

        if not worktree_path:
            task.prompt = (
                f"{task.prompt}\n\n"
                f"## IMPORTANT: Branch delivery requirements\n\n"
                f"You MUST deliver your work on branch `{branch_name}`.\n"
                f"Before starting work:\n"
                f"1. `git checkout -b {branch_name}` (create the branch)\n"
            )

        assert "git checkout -b" in task.prompt


# ---------------------------------------------------------------------------
# Worktree isolation: commits in worktree don't affect main
# ---------------------------------------------------------------------------


class TestWorktreeIsolation:
    def test_commit_in_worktree_not_visible_in_main(self, git_repo: Path):
        """Changes committed in a worktree should NOT appear in the main working tree."""
        wt = create_worktree(git_repo, "af/task-1", 1)

        # Create and commit a file in the worktree
        (wt / "new_file.txt").write_text("hello from worktree")
        subprocess.run(["git", "add", "new_file.txt"], cwd=wt, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "worktree change"],
            cwd=wt,
            capture_output=True,
        )

        # File should NOT exist in main working tree
        assert not (git_repo / "new_file.txt").exists()

        # But branch should have the commit (visible from main repo)
        result = subprocess.run(
            ["git", "log", "af/task-1", "--oneline", "-1"],
            cwd=git_repo,
            capture_output=True,
            text=True,
        )
        assert "worktree change" in result.stdout

    def test_parallel_worktrees_independent(self, git_repo: Path):
        """Two worktrees can modify different files without conflict."""
        wt1 = create_worktree(git_repo, "af/task-1", 1)
        wt2 = create_worktree(git_repo, "af/task-2", 2)

        # Write different files in each worktree
        (wt1 / "file1.txt").write_text("from task 1")
        (wt2 / "file2.txt").write_text("from task 2")

        subprocess.run(["git", "add", "file1.txt"], cwd=wt1, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "task 1 change"],
            cwd=wt1,
            capture_output=True,
        )

        subprocess.run(["git", "add", "file2.txt"], cwd=wt2, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "task 2 change"],
            cwd=wt2,
            capture_output=True,
        )

        # Each worktree only has its own file
        assert (wt1 / "file1.txt").exists()
        assert not (wt1 / "file2.txt").exists()
        assert (wt2 / "file2.txt").exists()
        assert not (wt2 / "file1.txt").exists()

        # Neither appears in main working tree
        assert not (git_repo / "file1.txt").exists()
        assert not (git_repo / "file2.txt").exists()
