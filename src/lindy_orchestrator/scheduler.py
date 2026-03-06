"""DAG-based parallel task execution with retry logic.

Executes tasks from a TaskPlan in dependency order, dispatching independent
tasks in parallel using concurrent.futures.
"""

from __future__ import annotations

import concurrent.futures
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .config import OrchestratorConfig
from .hooks import Event, EventType, HookRegistry, make_progress_adapter
from .logger import ActionLogger
from .mailbox import Mailbox, format_mailbox_messages
from .models import QACheck, TaskItem, TaskPlan, TaskStatus, plan_to_dict
from .providers import create_provider
from .qa import run_qa_gate
from .qa.feedback import format_qa_feedback


@dataclass
class ExecutionProgress:
    """Tracks overall execution progress."""

    total_tasks: int = 0
    completed: int = 0
    failed: int = 0
    skipped: int = 0
    in_progress: int = 0
    total_dispatches: int = 0
    start_time: float = field(default_factory=time.monotonic)

    @property
    def pending(self) -> int:
        return self.total_tasks - self.completed - self.failed - self.skipped - self.in_progress

    @property
    def elapsed_seconds(self) -> float:
        return time.monotonic() - self.start_time if self.start_time else 0.0


def execute_plan(
    plan: TaskPlan,
    config: OrchestratorConfig,
    logger: ActionLogger,
    on_progress: Callable[[str], None] | None = None,
    verbose: bool = False,
    hooks: HookRegistry | None = None,
    session_mgr: object | None = None,
    session: object | None = None,
) -> TaskPlan:
    """Execute a task plan with parallel dispatch and QA gates.

    Tasks are dispatched in dependency order. Independent tasks run in parallel
    up to config.safety.max_parallel workers.

    Returns the updated plan with task statuses and results.
    """
    max_retries = config.safety.max_retries_per_task
    max_parallel = config.safety.max_parallel
    total_dispatches = 0

    # Initialize hook registry with backward-compat progress adapter
    if hooks is None:
        hooks = HookRegistry()
    if on_progress:
        hooks.on_any(make_progress_adapter(on_progress))

    def progress(msg: str) -> None:
        if on_progress:
            on_progress(msg)

    def detail(msg: str) -> None:
        if on_progress and verbose:
            on_progress(msg)

    hooks.emit(Event(type=EventType.SESSION_START, data={"goal": plan.goal}))

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_parallel) as pool:
        while not plan.all_terminal():
            ready = plan.next_ready()
            if not ready:
                # No tasks ready but not all terminal — shouldn't happen
                # unless there's a dependency cycle. Break to avoid infinite loop.
                break

            if len(ready) > 1:
                progress(f"\n  [bold]Dispatching {len(ready)} tasks in parallel...[/]")

            # Submit all ready tasks
            futures: dict[concurrent.futures.Future, TaskItem] = {}
            for task in ready:
                task.status = TaskStatus.IN_PROGRESS
                task.started_at = datetime.now(timezone.utc).isoformat()
                progress(f"\n  [bold]Task {task.id}:[/] [{task.module}] {task.description}")

                if config.safety.dry_run:
                    task.status = TaskStatus.COMPLETED
                    task.result = "[DRY RUN] Skipped"
                    wd = config.module_path(task.module)
                    qa_list = ", ".join(q.gate for q in task.qa_checks) or "none"
                    deps = f" (after: {task.depends_on})" if task.depends_on else ""
                    progress(f"    [yellow]DRY RUN[/] — would dispatch to {task.module}")
                    progress(f"      Working dir: {wd}")
                    progress(f"      QA gates: {qa_list}")
                    progress(f"      Dependencies: {deps or 'none'}")
                    if task.prompt:
                        progress(f"      Prompt preview: {task.prompt[:150]}...")
                    continue

                future = pool.submit(
                    _execute_single_task,
                    task,
                    config,
                    logger,
                    progress,
                    detail,
                    max_retries,
                    hooks,
                )
                futures[future] = task

            # Wait for all parallel tasks to complete
            for future in concurrent.futures.as_completed(futures):
                task = futures[future]
                try:
                    dispatches = future.result()
                    total_dispatches += dispatches
                except Exception as e:
                    task.status = TaskStatus.FAILED
                    task.result = f"Unexpected error: {e}"
                    progress(f"    [bold red]Task {task.id} ERROR[/]: {e}")
                    logger.log_action(
                        "task_error",
                        details={"task_id": task.id, "error": str(e)},
                        result="error",
                    )

                # Checkpoint after each task resolves
                if session_mgr and session:
                    try:
                        session_mgr.checkpoint(session, plan_to_dict(plan))
                        hooks.emit(
                            Event(
                                type=EventType.CHECKPOINT_SAVED,
                                data={"checkpoint_count": session.checkpoint_count},
                            )
                        )
                    except Exception:
                        pass  # checkpoint failure should not stop execution

    hooks.emit(
        Event(
            type=EventType.SESSION_END,
            data={
                "goal": plan.goal,
                "total_dispatches": total_dispatches,
                "has_failures": plan.has_failures(),
            },
        )
    )
    return plan


def _execute_single_task(
    task: TaskItem,
    config: OrchestratorConfig,
    logger: ActionLogger,
    progress: Callable[[str], None],
    detail: Callable[[str], None],
    max_retries: int,
    hooks: HookRegistry | None = None,
) -> int:
    """Execute a single task with dispatch, QA gates, and retry logic.

    Returns the number of dispatches made.
    """
    # Auto-inject structural check gate
    has_structural = any(q.gate == "structural_check" for q in task.qa_checks)
    if not has_structural:
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
    arch_path = config.root / "ARCHITECTURE.md"
    if not has_layer and config.qa_gates.layer_check.enabled and arch_path.exists():
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

    # Auto-inject custom command gates if task has no command_check gates
    has_command = any(q.gate == "command_check" for q in task.qa_checks)
    if not has_command and config.qa_gates.custom:
        for gate in config.qa_gates.custom:
            task.qa_checks.append(
                QACheck(
                    gate="command_check",
                    params={"command": gate.command, "cwd": gate.cwd},
                )
            )
            progress(f"    [dim]Auto-injected QA: command_check ({gate.command})[/]")

    dispatches = 0

    while True:
        # Dispatch to module agent
        progress(f"    Dispatching to [bold]{task.module}[/] agent...")
        working_dir = config.module_path(task.module)

        # Heartbeat state for progress feedback
        _hb_count = 0
        _hb_last_tool = ""
        _hb_start = time.monotonic()
        _hb_last_print = _hb_start

        def _on_event(event: dict) -> None:
            """Track events and emit periodic heartbeat."""
            nonlocal _hb_count, _hb_last_tool, _hb_last_print
            _hb_count += 1

            tool_name = ""
            content = event.get("message", {}).get("content", [{}])
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        tool_name = block.get("name", "?")
                        _hb_last_tool = tool_name
                        detail(f"      [dim]tool: {tool_name}[/]")

            # Heartbeat: every 30 seconds
            now = time.monotonic()
            if now - _hb_last_print >= 30:
                elapsed = int(now - _hb_start)
                mins, secs = divmod(elapsed, 60)
                tool_hint = f", last tool: {_hb_last_tool}" if _hb_last_tool else ""
                progress(f"    [dim]⋯ {_hb_count} events, {mins}m{secs:02d}s{tool_hint}[/]")
                _hb_last_print = now

        # Inject pending mailbox messages if enabled
        if config.mailbox.enabled and config.mailbox.inject_on_dispatch:
            try:
                mb = Mailbox(config.root / config.mailbox.dir)
                pending = mb.receive(task.module, unread_only=True)
                if pending:
                    formatted = format_mailbox_messages(pending)
                    task.prompt = (
                        f"{task.prompt}\n\n"
                        f"## Inter-agent messages for {task.module}\n\n"
                        f"{formatted}\n"
                    )
                    progress(f"    [dim]Injected {len(pending)} mailbox message(s)[/]")
            except Exception:
                pass  # mailbox injection failure should not block dispatch

        provider = create_provider(config.dispatcher)
        result = provider.dispatch(
            module=task.module,
            working_dir=working_dir,
            prompt=task.prompt,
            on_event=_on_event,
        )
        dispatches += 1
        task.result = result.output

        logger.log_dispatch(
            task.module,
            task.prompt[:200],
            {
                "success": result.success,
                "duration": result.duration_seconds,
                "exit_code": result.exit_code,
                "event_count": result.event_count,
                "last_tool_use": result.last_tool_use,
            },
        )

        if not result.success:
            error_info = result.output[:200]
            if result.error == "stall":
                error_info = f"Agent stalled (last tool: {result.last_tool_use or 'none'})"
            progress(f"    [red]DISPATCH FAILED[/] ({result.error or 'error'}): {error_info}")
            task.status = TaskStatus.FAILED
            task.completed_at = datetime.now(timezone.utc).isoformat()
            if hooks:
                hooks.emit(
                    Event(
                        type=EventType.TASK_FAILED,
                        task_id=task.id,
                        module=task.module,
                        data={
                            "reason": result.error or "dispatch_error",
                            "description": task.description,
                        },
                    )
                )
            return dispatches

        progress(
            f"    [green]Dispatch completed[/] "
            f"({result.duration_seconds}s, {result.event_count} events)"
        )
        detail(f"    Output preview: {result.output[:500]}")

        # Delivery check: verify branch has commits
        branch_name = f"{config.project.branch_prefix}/task-{task.id}"
        delivery_ok, delivery_msg = _check_delivery(config.root, branch_name)
        if delivery_ok:
            progress(f"    [green]Delivery check[/]: {delivery_msg}")
        else:
            progress(f"    [yellow]Delivery check[/]: {delivery_msg}")
            logger.log_action(
                "delivery_check",
                details={"task_id": task.id, "branch": branch_name},
                result="warning",
                output=delivery_msg,
            )

        # Run QA gates (sequentially per task)
        all_qa_passed = True
        for qa in task.qa_checks:
            progress(f"    Running QA: [bold]{qa.gate}[/]...")

            qa_result = run_qa_gate(
                check=qa,
                project_root=config.root,
                module_name=task.module,
                task_output=task.result,
                custom_gates=config.qa_gates.custom,
                dispatcher_config=config.dispatcher,
                qa_module=config.qa_module(),
            )
            task.qa_results.append(qa_result)
            logger.log_qa(qa.gate, qa_result.passed, qa_result.output)

            if qa_result.passed:
                progress(f"      [green]PASS[/]: {qa_result.output[:100]}")
                if hooks:
                    hooks.emit(
                        Event(
                            type=EventType.QA_PASSED,
                            task_id=task.id,
                            module=task.module,
                            data={"gate": qa.gate, "output": qa_result.output[:200]},
                        )
                    )
            else:
                progress(f"      [red]FAIL[/]: {qa_result.output[:200]}")
                detail(f"      Full output: {qa_result.output}")
                all_qa_passed = False
                if hooks:
                    hooks.emit(
                        Event(
                            type=EventType.QA_FAILED,
                            task_id=task.id,
                            module=task.module,
                            data={"gate": qa.gate, "output": qa_result.output[:200]},
                        )
                    )

        if all_qa_passed:
            task.status = TaskStatus.COMPLETED
            task.completed_at = datetime.now(timezone.utc).isoformat()
            progress(f"    [bold green]Task {task.id} COMPLETED[/]")
            logger.log_action(
                "task_completed",
                details={
                    "task_id": task.id,
                    "module": task.module,
                    "description": task.description,
                },
            )
            if hooks:
                hooks.emit(
                    Event(
                        type=EventType.TASK_COMPLETED,
                        task_id=task.id,
                        module=task.module,
                        data={"description": task.description},
                    )
                )
            return dispatches

        # Retry with QA feedback
        task.retries += 1
        if task.retries > max_retries:
            task.status = TaskStatus.FAILED
            task.completed_at = datetime.now(timezone.utc).isoformat()
            progress(f"    [bold red]Task {task.id} FAILED[/] after {max_retries} retries")
            logger.log_action(
                "task_failed",
                details={
                    "task_id": task.id,
                    "module": task.module,
                    "retries": task.retries,
                    "qa_results": [
                        {"gate": r.gate, "passed": r.passed, "output": r.output[:200]}
                        for r in task.qa_results
                    ],
                },
            )
            if hooks:
                hooks.emit(
                    Event(
                        type=EventType.TASK_FAILED,
                        task_id=task.id,
                        module=task.module,
                        data={
                            "reason": "max_retries_exceeded",
                            "retries": task.retries,
                            "description": task.description,
                        },
                    )
                )
            return dispatches

        if hooks:
            hooks.emit(
                Event(
                    type=EventType.TASK_RETRYING,
                    task_id=task.id,
                    module=task.module,
                    data={
                        "retry": task.retries,
                        "max_retries": max_retries,
                        "description": task.description,
                    },
                )
            )

        # Augment prompt with structured remediation feedback
        failed_checks = [r for r in task.qa_results if not r.passed]
        feedback_parts = []
        for r in failed_checks:
            structured = format_qa_feedback(r.gate, r.output)
            feedback_parts.append(f"### {r.gate}\n{structured}")
        failure_detail = "\n\n".join(feedback_parts)
        task.prompt = (
            f"{task.prompt}\n\n"
            f"## IMPORTANT: Previous attempt failed QA verification\n\n"
            f"{failure_detail}\n\n"
            f"Fix these issues. Specific instructions:\n"
            f"- Actually RUN all scripts and commands (do not just create them)\n"
            f"- Ensure output files are generated before declaring success\n"
            f"- Verify your changes by running the relevant test/build commands\n"
            f"- If a CI check failed, check the branch was pushed and CI triggered\n"
        )
        task.qa_results = []
        progress(
            f"    [yellow]QA failed, retrying with feedback[/] ({task.retries}/{max_retries})..."
        )


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
