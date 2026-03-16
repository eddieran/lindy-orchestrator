"""Helper utilities for the task scheduler."""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Any, Callable

from .config import OrchestratorConfig
from .dispatch_core import extract_event_info  # canonical location; re-exported here
from .mailbox import Mailbox, format_mailbox_messages
from .models import QACheck, TaskItem

log = logging.getLogger(__name__)

__all__ = [
    "_autofill_ci_params",
    "_check_delivery",
    "build_prompt",
    "extract_event_info",
    "gather_branch_delivery",
    "gather_claude_md",
    "gather_mailbox_messages",
    "gather_status_content",
    "inject_branch_delivery",
    "inject_claude_md",
    "inject_mailbox_messages",
    "inject_qa_gates",
    "inject_status_content",
]


def _check_delivery(project_root: Path, branch_name: str) -> tuple[bool, str]:
    """Check if a branch exists and has new commits since the fork point.

    Uses `git merge-base` to find the correct fork point, avoiding false
    negatives when HEAD has advanced past the branch point.

    Returns (ok, message). ok is True if branch has commits; False is a warning
    (not a hard failure — the agent may have committed to a different branch).
    """
    try:
        # Check branch exists
        result = subprocess.run(
            ["git", "branch", "--list", branch_name],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if not result.stdout.strip():
            # Also check remote branches
            result = subprocess.run(
                ["git", "branch", "-r", "--list", f"*/{branch_name}"],
                cwd=project_root,
                capture_output=True,
                text=True,
                timeout=10,
            )
            if not result.stdout.strip():
                return False, f"Branch {branch_name} not found (local or remote)"

        # Find fork point via merge-base
        merge_result = subprocess.run(
            ["git", "merge-base", "HEAD", branch_name],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if merge_result.returncode != 0:
            # Fallback: branches may be unrelated; count all commits on branch
            merge_base = ""
        else:
            merge_base = merge_result.stdout.strip()

        # Count commits since fork point
        rev_range = f"{merge_base}..{branch_name}" if merge_base else branch_name
        result = subprocess.run(
            ["git", "rev-list", "--count", rev_range],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=10,
        )
        count = int(result.stdout.strip()) if result.stdout.strip() else 0
        if count == 0:
            return False, f"Branch {branch_name} exists but has no new commits"

        return True, f"Branch {branch_name}: {count} new commit(s)"
    except Exception as e:
        return False, f"Delivery check error: {e}"


def inject_qa_gates(
    task: TaskItem,
    config: object,
    progress: Callable[[str], None],
) -> None:
    """Auto-inject standard QA gates (structural, layer, command) into a task."""
    if task.skip_qa:
        progress("    [dim]QA gates skipped (skip_qa=true)[/]")
        return

    skip_gates = set(task.skip_gates) if hasattr(task, "skip_gates") else set()

    # Auto-inject structural check gate
    has_structural = any(q.gate == "structural_check" for q in task.qa_checks)
    if not has_structural and "structural_check" not in skip_gates:
        sc = config.qa_gates.structural
        task.qa_checks.append(
            QACheck(
                gate="structural_check",
                params={
                    "max_file_lines": sc.max_file_lines,
                    "enforce_module_boundary": sc.enforce_module_boundary,
                    "sensitive_patterns": sc.sensitive_patterns,
                },
            )
        )
        progress("    [dim]Auto-injected QA: structural_check[/]")

    # Auto-inject layer_check gate
    has_layer = any(q.gate == "layer_check" for q in task.qa_checks)
    arch_path = config.root / ".orchestrator" / "architecture.md"
    if (
        not has_layer
        and "layer_check" not in skip_gates
        and config.qa_gates.layer_check.enabled
        and arch_path.exists()
    ):
        task.qa_checks.append(
            QACheck(
                gate="layer_check",
                params={
                    "enabled": config.qa_gates.layer_check.enabled,
                    "unknown_file_policy": config.qa_gates.layer_check.unknown_file_policy,
                },
            )
        )
        progress("    [dim]Auto-injected QA: layer_check[/]")

    # Auto-inject custom command gates, skipping commands already present
    if config.qa_gates.custom:
        existing_commands = {
            q.params.get("command") for q in task.qa_checks if q.gate == "command_check"
        }
        for gate in config.qa_gates.custom:
            if gate.command in existing_commands:
                continue
            if gate.modules and task.module not in gate.modules:
                continue
            if gate.name in skip_gates:
                continue
            params: dict[str, Any] = {"command": gate.command, "cwd": gate.cwd}
            if not gate.required:
                params["required"] = False
            if gate.diff_only:
                params["diff_only"] = True
                # diff_only without {changed_files} placeholder can't filter —
                # auto-demote to non-required so pre-existing lint doesn't block
                if "{changed_files}" not in gate.command:
                    params["required"] = False
            task.qa_checks.append(QACheck(gate="command_check", params=params))
            progress(f"    [dim]Auto-injected QA: command_check ({gate.command})[/]")


def inject_mailbox_messages(
    task: TaskItem,
    config: OrchestratorConfig,
    progress: Callable[[str], None],
) -> None:
    """Inject pending mailbox messages into task prompt if enabled."""
    if not config.mailbox.enabled or not config.mailbox.inject_on_dispatch:
        return
    try:
        mb = Mailbox(config.root / config.mailbox.dir)
        pending = mb.receive(task.module, unread_only=True)
        if pending:
            formatted = format_mailbox_messages(pending)
            task.prompt = (
                f"{task.prompt}\n\n## Inter-agent messages for {task.module}\n\n{formatted}\n"
            )
            progress(f"    [dim]Injected {len(pending)} mailbox message(s)[/]")
    except Exception:
        log.warning("Mailbox injection failed for %s", task.module, exc_info=True)


def inject_claude_md(
    task: TaskItem,
    config: OrchestratorConfig,
    progress: Callable[[str], None],
) -> None:
    """Inject CLAUDE.md / CODEX.md instructions (root + module-specific) into task prompt.

    Provider-aware: reads from `.orchestrator/codex/` for codex_cli,
    `.orchestrator/claude/` for claude_cli, with fallback to `claude/`.
    """
    provider = config.dispatcher.provider
    if provider == "codex_cli":
        provider_dir = "codex"
        header_label = "CODEX.md"
    else:
        provider_dir = "claude"
        header_label = "CLAUDE.md"

    orch_base = config._config_dir / ".orchestrator"
    primary = orch_base / provider_dir
    fallback = orch_base / "claude" if provider_dir != "claude" else None

    sections: list[str] = []
    for name in ("root.md", f"{task.module}.md"):
        path = primary / name
        if not path.exists() and fallback:
            path = fallback / name
        if path.exists():
            try:
                sections.append(path.read_text())
            except Exception:
                log.warning("Failed to read %s", path, exc_info=True)
    if sections:
        header = f"## {header_label} Instructions\n\n" + "\n\n".join(sections)
        task.prompt = f"{header}\n\n{task.prompt}"
        progress(f"    [dim]Injected {header_label} instructions[/]")


def inject_status_content(
    task: TaskItem,
    config: OrchestratorConfig,
    progress: Callable[[str], None],
) -> None:
    """Inject STATUS.md content for the task's module into the prompt."""
    path = config._config_dir / ".orchestrator" / "status" / f"{task.module}.md"
    if not path.exists():
        return
    try:
        content = path.read_text()
    except Exception:
        log.warning("Failed to read %s", path, exc_info=True)
        return
    task.prompt = f"## Current STATUS.md\n\n{content}\n\n{task.prompt}"
    progress(f"    [dim]Injected STATUS.md for {task.module}[/]")


def inject_branch_delivery(
    task: TaskItem,
    branch_name: str,
    worktree_path: Path | None,
    dispatches: int,
) -> None:
    """Inject branch delivery instructions into task prompt on first dispatch."""
    if dispatches != 0:
        return
    if worktree_path:
        task.prompt = (
            f"{task.prompt}\n\n"
            f"## IMPORTANT: Branch delivery requirements\n\n"
            f"You are already on branch `{branch_name}` (worktree isolation).\n"
            f"Do NOT switch branches or run `git checkout`.\n"
            f"When done:\n"
            f"1. `git add` and `git commit` your changes\n"
            f"2. `git push -u origin {branch_name}` (push to remote)\n"
            f"Do NOT skip the push step — CI verification depends on it.\n"
        )
    else:
        task.prompt = (
            f"{task.prompt}\n\n"
            f"## IMPORTANT: Branch delivery requirements\n\n"
            f"You MUST deliver your work on branch `{branch_name}`.\n"
            f"Before starting work:\n"
            f"1. `git checkout -b {branch_name}` (create the branch)\n"
            f"When done:\n"
            f"2. `git add` and `git commit` your changes\n"
            f"3. `git push -u origin {branch_name}` (push to remote)\n"
            f"Do NOT skip the push step — CI verification depends on it.\n"
        )


def _autofill_ci_params(
    qa_checks: list[QACheck],
    branch_name: str,
    config: OrchestratorConfig,
    module_name: str,
) -> None:
    """Auto-fill ci_check branch/repo params if missing."""
    for qa in qa_checks:
        if qa.gate != "ci_check":
            continue
        if not qa.params.get("branch"):
            qa.params["branch"] = branch_name
        if not qa.params.get("repo"):
            try:
                mod_cfg = config.get_module(module_name)
                if mod_cfg.repo:
                    qa.params["repo"] = mod_cfg.repo
            except ValueError:
                pass


# ---------------------------------------------------------------------------
# gather_* variants — return strings instead of mutating task.prompt
# ---------------------------------------------------------------------------


def gather_status_content(task: TaskItem, config: OrchestratorConfig) -> str:
    """Return STATUS.md content for the task's module, or empty string."""
    path = config._config_dir / ".orchestrator" / "status" / f"{task.module}.md"
    if not path.exists():
        return ""
    try:
        content = path.read_text()
    except Exception:
        log.warning("Failed to read %s", path, exc_info=True)
        return ""
    return f"## Current STATUS.md\n\n{content}"


def gather_claude_md(task: TaskItem, config: OrchestratorConfig) -> str:
    """Return CLAUDE.md / CODEX.md instructions, or empty string."""
    provider = config.dispatcher.provider
    if provider == "codex_cli":
        provider_dir = "codex"
        header_label = "CODEX.md"
    else:
        provider_dir = "claude"
        header_label = "CLAUDE.md"

    orch_base = config._config_dir / ".orchestrator"
    primary = orch_base / provider_dir
    fallback = orch_base / "claude" if provider_dir != "claude" else None

    sections: list[str] = []
    for name in ("root.md", f"{task.module}.md"):
        path = primary / name
        if not path.exists() and fallback:
            path = fallback / name
        if path.exists():
            try:
                sections.append(path.read_text())
            except Exception:
                log.warning("Failed to read %s", path, exc_info=True)
    if not sections:
        return ""
    return f"## {header_label} Instructions\n\n" + "\n\n".join(sections)


def gather_mailbox_messages(task: TaskItem, config: OrchestratorConfig) -> str:
    """Return formatted mailbox messages, or empty string."""
    if not config.mailbox.enabled or not config.mailbox.inject_on_dispatch:
        return ""
    try:
        mb = Mailbox(config.root / config.mailbox.dir)
        pending = mb.receive(task.module, unread_only=True)
        if pending:
            formatted = format_mailbox_messages(pending)
            return f"## Inter-agent messages for {task.module}\n\n{formatted}"
    except Exception:
        log.warning("Mailbox injection failed for %s", task.module, exc_info=True)
    return ""


def gather_branch_delivery(
    task: TaskItem,
    branch_name: str,
    worktree_path: Path | None,
    dispatches: int,
) -> str:
    """Return branch delivery instructions, or empty string."""
    if dispatches != 0:
        return ""
    if worktree_path:
        return (
            f"## IMPORTANT: Branch delivery requirements\n\n"
            f"You are already on branch `{branch_name}` (worktree isolation).\n"
            f"Do NOT switch branches or run `git checkout`.\n"
            f"When done:\n"
            f"1. `git add` and `git commit` your changes\n"
            f"2. `git push -u origin {branch_name}` (push to remote)\n"
            f"Do NOT skip the push step — CI verification depends on it."
        )
    return (
        f"## IMPORTANT: Branch delivery requirements\n\n"
        f"You MUST deliver your work on branch `{branch_name}`.\n"
        f"Before starting work:\n"
        f"1. `git checkout -b {branch_name}` (create the branch)\n"
        f"When done:\n"
        f"2. `git add` and `git commit` your changes\n"
        f"3. `git push -u origin {branch_name}` (push to remote)\n"
        f"Do NOT skip the push step — CI verification depends on it."
    )


def build_prompt(
    task: TaskItem,
    config: OrchestratorConfig,
    branch_name: str,
    worktree_path: Path | None,
    dispatches: int,
    progress: Callable[[str], None],
) -> str:
    """Build the complete prompt for a task dispatch.

    If config.dispatcher.prompt_template is set, reads the template file and
    renders it with ``str.format_map()``. Falls back to default concatenation
    order on any error.
    """
    user_prompt = task.prompt

    # Gather sections
    if dispatches == 0:
        status_content = gather_status_content(task, config)
        claude_md = gather_claude_md(task, config)
    else:
        status_content = ""
        claude_md = ""

    mailbox_messages = gather_mailbox_messages(task, config)
    branch_instructions = gather_branch_delivery(task, branch_name, worktree_path, dispatches)

    # Log what was injected
    if status_content:
        progress(f"    [dim]Injected STATUS.md for {task.module}[/]")
    if claude_md:
        progress("    [dim]Injected CLAUDE.md instructions[/]")
    if mailbox_messages:
        progress("    [dim]Injected mailbox messages[/]")

    template_path = config.dispatcher.prompt_template
    if template_path:
        try:
            tpl = Path(template_path).read_text()
            return tpl.format_map(
                {
                    "status_content": status_content,
                    "claude_md": claude_md,
                    "mailbox_messages": mailbox_messages,
                    "branch_instructions": branch_instructions,
                    "user_prompt": user_prompt,
                }
            )
        except Exception:
            log.warning("Prompt template render failed, using default order", exc_info=True)

    # Default concatenation order
    parts = [
        p
        for p in [status_content, claude_md, user_prompt, mailbox_messages, branch_instructions]
        if p
    ]
    return "\n\n".join(parts)
