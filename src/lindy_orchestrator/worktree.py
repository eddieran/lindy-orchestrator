"""Git worktree management for parallel task isolation.

When multiple tasks execute in parallel, each agent needs its own working
directory to avoid git checkout race conditions. Git worktrees provide
this isolation while sharing the same repository data.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import threading
from pathlib import Path

log = logging.getLogger(__name__)

_lock = threading.Lock()

WORKTREES_DIR = ".worktrees"


def create_worktree(project_root: Path, branch_name: str, task_id: int) -> Path:
    """Create an isolated worktree for a task on its delivery branch.

    Thread-safe: serialized via a module-level lock so concurrent tasks
    don't race on git internal state.

    Returns the absolute path to the new worktree directory.
    """
    worktree_dir = project_root / WORKTREES_DIR / f"task-{task_id}"

    with _lock:
        # Clean up stale worktree from a previous run
        if worktree_dir.exists():
            _remove_worktree_unlocked(project_root, worktree_dir)

        worktree_dir.parent.mkdir(parents=True, exist_ok=True)

        # Create branch from HEAD if it doesn't exist yet
        _ensure_branch(project_root, branch_name)

        result = subprocess.run(
            ["git", "worktree", "add", str(worktree_dir), branch_name],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            # Branch already checked out elsewhere — fall back to detached HEAD
            # then create a new branch inside the worktree.
            if (
                "already used by worktree" in result.stderr
                or "already checked out" in result.stderr
            ):
                log.warning(
                    "Branch %s occupied, creating worktree in detached HEAD mode",
                    branch_name,
                )
                result2 = subprocess.run(
                    ["git", "worktree", "add", "--detach", str(worktree_dir)],
                    cwd=project_root,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                if result2.returncode != 0:
                    raise RuntimeError(
                        f"git worktree add --detach failed: {result2.stderr.strip()}"
                    )
                # Create a unique branch inside the new worktree
                wt_branch = f"worktree-task-{task_id}"
                subprocess.run(
                    ["git", "checkout", "-b", wt_branch],
                    cwd=str(worktree_dir),
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
            else:
                raise RuntimeError(
                    f"git worktree add failed for {branch_name}: {result.stderr.strip()}"
                )

    log.info("Created worktree at %s on branch %s", worktree_dir, branch_name)
    return worktree_dir.resolve()


def remove_worktree(project_root: Path, task_id: int) -> None:
    """Remove a task's worktree. Safe to call even if it doesn't exist."""
    worktree_dir = project_root / WORKTREES_DIR / f"task-{task_id}"
    with _lock:
        _remove_worktree_unlocked(project_root, worktree_dir)


def cleanup_all_worktrees(project_root: Path) -> None:
    """Remove every worktree under .worktrees/ and prune."""
    worktrees_base = project_root / WORKTREES_DIR
    if not worktrees_base.exists():
        return

    with _lock:
        for child in list(worktrees_base.iterdir()):
            if child.is_dir():
                _remove_worktree_unlocked(project_root, child)

        subprocess.run(
            ["git", "worktree", "prune"],
            cwd=project_root,
            capture_output=True,
            timeout=30,
        )

        # Remove the directory if empty
        try:
            if worktrees_base.exists() and not any(worktrees_base.iterdir()):
                worktrees_base.rmdir()
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _remove_worktree_unlocked(project_root: Path, worktree_dir: Path) -> None:
    """Remove a single worktree (caller holds the lock)."""
    if not worktree_dir.exists():
        return

    result = subprocess.run(
        ["git", "worktree", "remove", "--force", str(worktree_dir)],
        cwd=project_root,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        log.warning(
            "git worktree remove failed (%s), falling back to rm",
            result.stderr.strip(),
        )
        shutil.rmtree(worktree_dir, ignore_errors=True)
        subprocess.run(
            ["git", "worktree", "prune"],
            cwd=project_root,
            capture_output=True,
            timeout=30,
        )


def _ensure_branch(project_root: Path, branch_name: str) -> None:
    """Create branch from HEAD if it doesn't already exist."""
    result = subprocess.run(
        ["git", "branch", "--list", branch_name],
        cwd=project_root,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if not result.stdout.strip():
        subprocess.run(
            ["git", "branch", branch_name],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=10,
        )
