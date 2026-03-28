"""DAG-based parallel task execution with retry logic.

Executes tasks from a TaskPlan in dependency order, dispatching independent
tasks in parallel using concurrent.futures.
"""

from __future__ import annotations

import collections
import concurrent.futures
import logging
import re
import shlex
import signal
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .config import OrchestratorConfig
from .generator_runner import GeneratorRunner
from .scheduler_helpers import (
    _autofill_ci_params,
    _check_delivery,
    extract_event_info,
    prepare_qa_checks,
)
from .hooks import Event, EventType, HookRegistry, make_progress_adapter
from .logger import ActionLogger
from .metrics import MetricsCollector
from .models import TaskSpec, TaskPlan, TaskStatus, plan_to_dict
from .providers import create_provider
from .qa import run_qa_gate
from .qa.feedback import StructuredFeedback, build_retry_prompt, build_structured_feedback
from .worktree import cleanup_all_worktrees, create_worktree, remove_worktree

log = logging.getLogger(__name__)

# Shell metacharacters that require sh -c wrapping
_SHELL_META_RE = re.compile(r"&&|\|\||[|;<>]")


def _run_lifecycle_hook(
    hook_name: str,
    command: str,
    working_dir: Path,
    progress: Callable[[str], None],
    timeout: int = 60,
) -> bool:
    """Run a lifecycle hook command. Returns True on success, False on failure.

    Hook failure is non-blocking — logged as a warning, never raises.
    """
    if not command:
        return True

    try:
        if _SHELL_META_RE.search(command):
            cmd_args = ["sh", "-c", command]
        else:
            cmd_args = shlex.split(command)

        proc = subprocess.run(
            cmd_args,
            cwd=str(working_dir),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if proc.returncode != 0:
            log.warning(
                "Lifecycle hook %s failed (exit %d): %s",
                hook_name,
                proc.returncode,
                proc.stderr[:200],
            )
            progress(f"    [yellow]Hook {hook_name} failed (exit {proc.returncode})[/]")
            return False
        progress(f"    [dim]Hook {hook_name} ok[/]")
        return True
    except subprocess.TimeoutExpired:
        log.warning("Lifecycle hook %s timed out after %ds", hook_name, timeout)
        progress(f"    [yellow]Hook {hook_name} timed out ({timeout}s)[/]")
        return False
    except Exception as e:
        log.warning("Lifecycle hook %s error: %s", hook_name, e)
        progress(f"    [yellow]Hook {hook_name} error: {e}[/]")
        return False


# ---------------------------------------------------------------------------
# HeartbeatTracker — replaces closure with 5 nonlocal variables
# ---------------------------------------------------------------------------


class _HeartbeatTracker:
    """Tracks event progress and emits periodic heartbeat messages."""

    __slots__ = (
        "task_id",
        "module",
        "_progress",
        "_detail",
        "_hooks",
        "count",
        "last_tool",
        "recent_tools",
        "last_reasoning",
        "_start_time",
        "_last_print_time",
    )

    def __init__(
        self,
        task_id: int,
        module: str,
        progress: Callable[[str], None],
        detail: Callable[[str], None],
        hooks: HookRegistry | None,
    ) -> None:
        self.task_id = task_id
        self.module = module
        self._progress = progress
        self._detail = detail
        self._hooks = hooks
        self.count = 0
        self.last_tool = ""
        self.recent_tools: collections.deque[str] = collections.deque(maxlen=5)
        self.last_reasoning = ""
        self._start_time = time.monotonic()
        self._last_print_time = self._start_time

    def on_event(self, event: dict) -> None:
        """Track events and emit periodic heartbeat."""
        self.count += 1

        tool_name, reasoning_text = extract_event_info(event)

        if tool_name:
            self.last_tool = tool_name
            self.recent_tools.append(tool_name)
            self._detail(f"      [dim]tool: {tool_name}[/]")

        if reasoning_text:
            self.last_reasoning = reasoning_text

        if self._hooks and (tool_name or reasoning_text):
            self._hooks.emit(
                Event(
                    type=EventType.TASK_HEARTBEAT,
                    task_id=self.task_id,
                    module=self.module,
                    data={
                        "tool": tool_name,
                        "tools": list(self.recent_tools),
                        "event_count": self.count,
                        "reasoning": self.last_reasoning[:120] if self.last_reasoning else "",
                    },
                )
            )

        now = time.monotonic()
        if now - self._last_print_time >= 30:
            elapsed = int(now - self._start_time)
            mins, secs = divmod(elapsed, 60)
            tool_hint = f", last tool: {self.last_tool}" if self.last_tool else ""
            self._progress(f"    [dim]⋯ {self.count} events, {mins}m{secs:02d}s{tool_hint}[/]")
            self._last_print_time = now


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

    # Per-module concurrency semaphores
    module_semaphores: dict[str, threading.Semaphore] = {
        mod: threading.Semaphore(limit) for mod, limit in config.safety.module_concurrency.items()
    }

    # Initialize hook registry with backward-compat progress adapter
    if hooks is None:
        hooks = HookRegistry()
    if on_progress:
        hooks.on_any(make_progress_adapter(on_progress))

    # Attach metrics collector before SESSION_START
    metrics = MetricsCollector()
    metrics.attach(hooks)

    def progress(msg: str) -> None:
        if on_progress:
            on_progress(msg)

    def detail(msg: str) -> None:
        if on_progress and verbose:
            on_progress(msg)

    otel_exporter = None

    hooks.emit(Event(type=EventType.SESSION_START, data={"goal": plan.goal}))

    # Install signal handler so worktrees are cleaned up on Ctrl-C / SIGTERM
    _interrupted = False
    prev_sigint = signal.getsignal(signal.SIGINT)
    prev_sigterm = signal.getsignal(signal.SIGTERM)

    def _on_signal(signum, frame):  # type: ignore[no-untyped-def]
        nonlocal _interrupted
        _interrupted = True
        log.warning("Received signal %s — will clean up worktrees", signum)

    try:
        signal.signal(signal.SIGINT, _on_signal)
        signal.signal(signal.SIGTERM, _on_signal)
    except (OSError, ValueError):
        # signal handlers can only be set from the main thread
        pass

    try:
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=max_parallel) as pool:
                while not plan.all_terminal() and not _interrupted:
                    # Hot-reload config (safe sections only)
                    if config.check_reload() is not None:
                        max_retries = config.safety.max_retries_per_task
                        log.info("Config hot-reloaded")

                    ready = plan.next_ready()

                    # Emit TASK_SKIPPED for newly skipped tasks (guarded by completed_at)
                    had_new_skips = False
                    for t in plan.tasks:
                        if t.status == TaskStatus.SKIPPED and t.completed_at is None:
                            t.completed_at = datetime.now(timezone.utc).isoformat()
                            had_new_skips = True
                            hooks.emit(
                                Event(
                                    type=EventType.TASK_SKIPPED,
                                    task_id=t.id,
                                    module=t.module,
                                    data={
                                        "reason": t.result,
                                        "description": t.description,
                                    },
                                )
                            )
                            progress(
                                f"    [dim]Task {t.id} SKIPPED[/] ({t.module}): {t.description}"
                            )

                    if not ready:
                        if had_new_skips:
                            continue  # cascade: re-check for more skips
                        break

                    if len(ready) > 1:
                        progress(f"\n  [bold]Dispatching {len(ready)} tasks in parallel...[/]")

                    futures: dict[concurrent.futures.Future, TaskSpec] = {}
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
                            module_semaphores.get(task.module),
                        )
                        futures[future] = task

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

            if _interrupted:
                progress("\n  [bold yellow]Interrupted — marking in-progress tasks as failed[/]")
                for task in plan.tasks:
                    if task.status == TaskStatus.IN_PROGRESS:
                        task.status = TaskStatus.FAILED
                        task.result = "Interrupted by signal"
        finally:
            # Always clean up worktrees, even on crash / interrupt
            try:
                cleanup_all_worktrees(config.root)
            except Exception:
                log.warning("Worktree cleanup failed", exc_info=True)

            # Restore original signal handlers
            try:
                signal.signal(signal.SIGINT, prev_sigint)
                signal.signal(signal.SIGTERM, prev_sigterm)
            except (OSError, ValueError):
                pass

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

        # Detach OTel exporter before cleanup
        if otel_exporter is not None:
            try:
                otel_exporter.detach()
            except Exception:
                log.warning("OTel exporter detach failed", exc_info=True)

        # Detach metrics collector
        metrics.detach(hooks)

        return plan
    finally:
        hooks.shutdown()


def _execute_single_task(
    task: TaskSpec,
    config: OrchestratorConfig,
    logger: ActionLogger,
    progress: Callable[[str], None],
    detail: Callable[[str], None],
    max_retries: int,
    hooks: HookRegistry | None = None,
    module_semaphore: threading.Semaphore | None = None,
) -> int:
    """Execute a single task with dispatch, QA gates, and retry logic.

    Returns the number of dispatches made.
    """
    if module_semaphore:
        module_semaphore.acquire()

    try:
        return _execute_single_task_inner(
            task, config, logger, progress, detail, max_retries, hooks
        )
    finally:
        if module_semaphore:
            module_semaphore.release()


def _execute_single_task_inner(
    task: TaskSpec,
    config: OrchestratorConfig,
    logger: ActionLogger,
    progress: Callable[[str], None],
    detail: Callable[[str], None],
    max_retries: int,
    hooks: HookRegistry | None = None,
) -> int:
    """Core single-task execution (inside optional semaphore)."""
    prepare_qa_checks(task, config, progress)

    branch_name = f"{config.project.branch_prefix}/task-{task.id}"

    # Create isolated worktree for parallel safety
    worktree_path: Path | None = None
    try:
        worktree_path = create_worktree(config.root, branch_name, task.id)
        progress(f"    [dim]Worktree: .worktrees/task-{task.id}[/]")
    except Exception as e:
        log.warning("Worktree creation failed, using shared directory: %s", e)

    lc = config.lifecycle_hooks
    hook_cwd = worktree_path or config.root

    if worktree_path and lc.after_create:
        _run_lifecycle_hook("after_create", lc.after_create, hook_cwd, progress, lc.timeout)

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
            if lc.before_remove:
                _run_lifecycle_hook(
                    "before_remove", lc.before_remove, hook_cwd, progress, lc.timeout
                )
            try:
                remove_worktree(config.root, task.id)
            except Exception:
                log.warning("Worktree cleanup failed for task-%d", task.id, exc_info=True)


# ---------------------------------------------------------------------------
# Extracted helpers for _dispatch_loop decomposition
# ---------------------------------------------------------------------------


def _log_dispatch(logger: ActionLogger, task: TaskSpec, result: object) -> None:
    """Log dispatch result to action logger."""
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


def _handle_dispatch_failure(
    task: TaskSpec,
    result: object,
    progress: Callable[[str], None],
    hooks: HookRegistry | None,
) -> None:
    """Mark task as failed and emit events on dispatch failure."""
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


def _check_and_log_delivery(
    project_root: Path,
    branch_name: str,
    logger: ActionLogger,
    task: TaskSpec,
    progress: Callable[[str], None],
) -> None:
    """Verify branch has commits and log the result."""
    delivery_ok, delivery_msg = _check_delivery(project_root, branch_name)
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


def _run_qa_gates(
    task: TaskSpec,
    config: OrchestratorConfig,
    logger: ActionLogger,
    qa_root: Path,
    module_dir: Path,
    progress: Callable[[str], None],
    detail: Callable[[str], None],
    hooks: HookRegistry | None,
) -> bool:
    """Run QA gates in parallel. Returns True if all pass."""
    gate_count = len(task.qa_checks)
    if gate_count == 0:
        progress("    [dim]No QA gates to run[/]")
        return True

    qa_mod = config.qa_module()
    progress(f"    Running {gate_count} QA gate(s){'  in parallel' if gate_count > 1 else ''}...")

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

    all_passed = True
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(gate_count, 4)) as qa_pool:
        futs = {qa_pool.submit(_run_gate, qa): qa for qa in task.qa_checks}
        for fut in concurrent.futures.as_completed(futs):
            qa, qa_result = fut.result()
            task.qa_results.append(qa_result)
            logger.log_qa(qa.gate, qa_result.passed, qa_result.output)
            evt_type = EventType.QA_PASSED if qa_result.passed else EventType.QA_FAILED
            is_required = qa.params.get("required", True)
            if qa_result.passed:
                progress(f"      [green]PASS[/]: {qa.gate} — {qa_result.output[:100]}")
            elif not is_required:
                progress(f"      [yellow]WARN[/]: {qa.gate} (optional) — {qa_result.output[:200]}")
            else:
                progress(f"      [red]FAIL[/]: {qa.gate} — {qa_result.output[:200]}")
                detail(f"      Full output: {qa_result.output}")
                all_passed = False
            if hooks:
                hooks.emit(
                    Event(
                        type=evt_type,
                        task_id=task.id,
                        module=task.module,
                        data={"gate": qa.gate, "output": qa_result.output[:200]},
                    )
                )
    return all_passed


def _mark_completed(
    task: TaskSpec,
    logger: ActionLogger,
    progress: Callable[[str], None],
    hooks: HookRegistry | None,
) -> None:
    """Mark task as completed and emit events."""
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


def _handle_retry(
    task: TaskSpec,
    original_prompt: str,
    max_retries: int,
    logger: ActionLogger,
    progress: Callable[[str], None],
    hooks: HookRegistry | None,
) -> bool:
    """Handle retry logic after QA failure. Returns True to continue loop, False to stop."""
    # If all failures are non-retryable (pre-existing violations), skip retry entirely
    failed_results = [r for r in task.qa_results if not r.passed]
    if failed_results and all(not r.retryable for r in failed_results):
        task.status = TaskStatus.FAILED
        task.completed_at = datetime.now(timezone.utc).isoformat()
        progress(
            f"    [bold red]Task {task.id} FAILED[/] — "
            f"all {len(failed_results)} failure(s) are pre-existing (non-retryable)"
        )
        if hooks:
            hooks.emit(
                Event(
                    type=EventType.TASK_FAILED,
                    task_id=task.id,
                    module=task.module,
                    data={
                        "reason": "non_retryable_failures",
                        "description": task.description,
                    },
                )
            )
        return False

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
        return False

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

    # Build structured feedback for each failed gate, filtering out
    # pre-existing violations when we can determine changed files.
    failed_checks = [r for r in task.qa_results if not r.passed]
    feedback_objs: list[StructuredFeedback] = []
    for r in failed_checks:
        fb = build_structured_feedback(
            r.gate,
            r.output,
            retry_number=task.retries,
            changed_files=r.details.get("changed_files"),
        )
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
    progress(f"    [yellow]QA failed, retrying with feedback[/] ({task.retries}/{max_retries})...")
    return True


# ---------------------------------------------------------------------------
# Dispatch loop (decomposed)
# ---------------------------------------------------------------------------


def _dispatch_loop(
    task: TaskSpec,
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
    runner = GeneratorRunner(config)
    original_prompt = runner.generator_prompt(task)
    _, module_dir = runner.resolve_working_dir(task, worktree_path)

    lc = config.lifecycle_hooks
    hook_cwd = worktree_path or config.root

    while True:
        progress(f"    Dispatching to [bold]{task.module}[/] agent...")

        hb = _HeartbeatTracker(task.id, task.module, progress, detail, hooks)

        if lc.before_run:
            _run_lifecycle_hook("before_run", lc.before_run, hook_cwd, progress, lc.timeout)
        result, module_dir = runner.dispatch(
            task=task,
            branch_name=branch_name,
            worktree_path=worktree_path,
            dispatches=dispatches,
            progress=progress,
            on_event=hb.on_event,
        )
        dispatches += 1
        task.result = result.output
        task.cost_usd += result.cost_usd
        _log_dispatch(logger, task, result)

        if result.success and lc.after_run:
            _run_lifecycle_hook("after_run", lc.after_run, hook_cwd, progress, lc.timeout)

        if not result.success:
            _handle_dispatch_failure(task, result, progress, hooks)
            return dispatches

        progress(
            f"    [green]Dispatch completed[/] "
            f"({result.duration_seconds}s, {result.event_count} events)"
        )
        detail(f"    Output preview: {result.output[:500]}")

        if task.skip_qa:
            # skip_qa tasks skip delivery check and QA gates entirely
            _mark_completed(task, logger, progress, hooks)
            return dispatches

        _check_and_log_delivery(config.root, branch_name, logger, task, progress)
        _autofill_ci_params(task.qa_checks, branch_name, config, task.module)

        qa_root = worktree_path or config.root
        all_qa_passed = _run_qa_gates(
            task,
            config,
            logger,
            qa_root,
            module_dir,
            progress,
            detail,
            hooks,
        )

        if all_qa_passed:
            _mark_completed(task, logger, progress, hooks)
            return dispatches

        if not _handle_retry(task, original_prompt, max_retries, logger, progress, hooks):
            return dispatches


class Orchestrator:
    """Wrapper around the execution engine."""

    def __init__(self, config: OrchestratorConfig):
        self.config = config

    def run(
        self,
        plan: TaskPlan,
        logger: ActionLogger,
        on_progress: Callable[[str], None] | None = None,
        verbose: bool = False,
        hooks: HookRegistry | None = None,
    ) -> TaskPlan:
        return execute_plan(
            plan,
            self.config,
            logger,
            on_progress=on_progress,
            verbose=verbose,
            hooks=hooks,
        )
