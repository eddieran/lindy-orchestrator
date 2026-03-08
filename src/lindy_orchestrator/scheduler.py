"""DAG-based parallel task execution with retry logic.

Executes tasks from a TaskPlan in dependency order, dispatching independent
tasks in parallel using concurrent.futures.
"""

from __future__ import annotations

import concurrent.futures
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .config import OrchestratorConfig
from .scheduler_helpers import (
    _autofill_ci_params,
    _check_delivery,
    inject_branch_delivery,
    inject_mailbox_messages,
    inject_qa_gates,
)
from .hooks import Event, EventType, HookRegistry, make_progress_adapter
from .logger import ActionLogger
from .models import TaskItem, TaskPlan, TaskStatus, plan_to_dict
from .providers import create_provider
from .qa import run_qa_gate
from .qa.feedback import StructuredFeedback, build_retry_prompt, build_structured_feedback
from .worktree import create_worktree, remove_worktree

log = logging.getLogger(__name__)


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
    # Pre-flight: validate provider is available before starting execution
    if not config.safety.dry_run:
        provider = create_provider(config.dispatcher)
        if hasattr(provider, "validate"):
            provider.validate()

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
                hooks.emit(
                    Event(
                        type=EventType.TASK_STARTED,
                        task_id=task.id,
                        module=task.module,
                        data={"description": task.description},
                    )
                )
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
                        log.warning("Checkpoint save failed", exc_info=True)

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
    inject_qa_gates(task, config, progress)

    branch_name = f"{config.project.branch_prefix}/task-{task.id}"

    # Create isolated worktree for parallel safety
    worktree_path: Path | None = None
    try:
        worktree_path = create_worktree(config.root, branch_name, task.id)
        progress(f"    [dim]Worktree: .worktrees/task-{task.id}[/]")
    except Exception as e:
        log.warning("Worktree creation failed, using shared directory: %s", e)

    try:
        return _dispatch_loop(
            task,
            config,
            logger,
            progress,
            detail,
            max_retries,
            hooks,
            branch_name,
            worktree_path,
        )
    finally:
        if worktree_path:
            try:
                remove_worktree(config.root, task.id)
            except Exception:
                log.warning("Worktree cleanup failed for task-%d", task.id, exc_info=True)


def _dispatch_loop(
    task: TaskItem,
    config: OrchestratorConfig,
    logger: ActionLogger,
    progress: Callable[[str], None],
    detail: Callable[[str], None],
    max_retries: int,
    hooks: HookRegistry | None,
    branch_name: str,
    worktree_path: Path | None,
) -> int:
    """Inner dispatch loop with retry logic. Extracted for worktree cleanup."""
    dispatches = 0
    original_prompt = task.prompt

    while True:
        # Dispatch to module agent
        progress(f"    Dispatching to [bold]{task.module}[/] agent...")

        # Use worktree for isolation; fall back to project root
        if worktree_path:
            working_dir = worktree_path
            if task.module in ("root", "*"):
                module_dir = worktree_path
            else:
                mod = config.get_module(task.module)
                module_dir = (worktree_path / mod.path).resolve()
        else:
            working_dir = config.root.resolve()
            module_dir = config.module_path(task.module)

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

            if hooks and tool_name:
                hooks.emit(
                    Event(
                        type=EventType.TASK_HEARTBEAT,
                        task_id=task.id,
                        module=task.module,
                        data={"tool": tool_name, "event_count": _hb_count},
                    )
                )

            # Heartbeat: every 30 seconds
            now = time.monotonic()
            if now - _hb_last_print >= 30:
                elapsed = int(now - _hb_start)
                mins, secs = divmod(elapsed, 60)
                tool_hint = f", last tool: {_hb_last_tool}" if _hb_last_tool else ""
                progress(f"    [dim]⋯ {_hb_count} events, {mins}m{secs:02d}s{tool_hint}[/]")
                _hb_last_print = now

        # Inject pending mailbox messages if enabled
        inject_mailbox_messages(task, config, progress)

        # Inject branch delivery instructions
        inject_branch_delivery(task, branch_name, worktree_path, dispatches)

        provider = create_provider(config.dispatcher)
        result = provider.dispatch(
            module=task.module,
            working_dir=working_dir,
            prompt=task.prompt,
            on_event=_on_event,
            stall_seconds=task.stall_seconds,
        )
        dispatches += 1
        task.result = result.output
        task.cost_usd += result.cost_usd

        logger.log_dispatch(
            task.module,
            task.prompt[:200],
            {
                "success": result.success,
                "duration": result.duration_seconds,
                "exit_code": result.exit_code,
                "event_count": result.event_count,
                "last_tool_use": result.last_tool_use,
                "cost_usd": result.cost_usd,
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

        # Auto-fill ci_check params before parallel execution
        _autofill_ci_params(task.qa_checks, branch_name, config, task.module)

        # Run QA gates in parallel
        qa_root = worktree_path or config.root
        qa_mod = config.qa_module()
        gate_count = len(task.qa_checks)
        progress(
            f"    Running {gate_count} QA gate(s){'  in parallel' if gate_count > 1 else ''}..."
        )

        def _run_gate(qa):
            return qa, run_qa_gate(
                check=qa,
                project_root=qa_root,
                module_name=task.module,
                task_output=task.result,
                custom_gates=config.qa_gates.custom,
                dispatcher_config=config.dispatcher,
                qa_module=qa_mod,
                module_path=module_dir,
            )

        all_qa_passed = True
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(gate_count, 4)) as qa_pool:
            futs = {qa_pool.submit(_run_gate, qa): qa for qa in task.qa_checks}
            for fut in concurrent.futures.as_completed(futs):
                qa, qa_result = fut.result()
                task.qa_results.append(qa_result)
                logger.log_qa(qa.gate, qa_result.passed, qa_result.output)
                evt_type = EventType.QA_PASSED if qa_result.passed else EventType.QA_FAILED
                if qa_result.passed:
                    progress(f"      [green]PASS[/]: {qa.gate} — {qa_result.output[:100]}")
                else:
                    progress(f"      [red]FAIL[/]: {qa.gate} — {qa_result.output[:200]}")
                    detail(f"      Full output: {qa_result.output}")
                    all_qa_passed = False
                if hooks:
                    hooks.emit(
                        Event(
                            type=evt_type,
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

        # Build structured feedback for each failed gate
        failed_checks = [r for r in task.qa_results if not r.passed]
        feedback_objs: list[StructuredFeedback] = []
        for r in failed_checks:
            fb = build_structured_feedback(r.gate, r.output, retry_number=task.retries)
            feedback_objs.append(fb)
            task.feedback_history.append(
                {
                    "retry": task.retries,
                    "gate": r.gate,
                    "category": fb.category.value,
                    "summary": fb.summary,
                    "errors": fb.specific_errors,
                    "files": fb.files_to_check,
                    "remediation": fb.remediation_steps,
                }
            )
        task.prompt = build_retry_prompt(original_prompt, feedback_objs, task.retries, max_retries)
        task.qa_results = []
        progress(
            f"    [yellow]QA failed, retrying with feedback[/] ({task.retries}/{max_retries})..."
        )
