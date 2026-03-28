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
from .evaluator_runner import EvaluatorRunner
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
from .models import EvalFeedback, EvalResult, TaskSpec, TaskPlan, TaskStatus, plan_to_dict
from .providers import create_provider
from .qa import run_qa_gate
from .worktree import cleanup_all_worktrees, create_worktree, remove_worktree

log = logging.getLogger(__name__)

# Shell metacharacters that require sh -c wrapping
_SHELL_META_RE = re.compile(r"&&|\|\||[|;<>]")


class CommandQueue:
    """Thread-safe command bus for runtime task controls."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._paused = False
        self._skip_ids: set[int] = set()
        self._force_pass_ids: set[int] = set()

    def pause(self) -> None:
        with self._lock:
            self._paused = True

    def resume(self) -> None:
        with self._lock:
            self._paused = False

    def skip(self, task_id: int) -> None:
        with self._lock:
            self._skip_ids.add(task_id)

    def force_pass(self, task_id: int) -> None:
        with self._lock:
            self._force_pass_ids.add(task_id)

    @property
    def is_paused(self) -> bool:
        with self._lock:
            return self._paused

    def pop_skip(self, task_id: int) -> bool:
        with self._lock:
            if task_id in self._skip_ids:
                self._skip_ids.remove(task_id)
                return True
            return False

    def pop_force_pass(self, task_id: int) -> bool:
        with self._lock:
            if task_id in self._force_pass_ids:
                self._force_pass_ids.remove(task_id)
                return True
            return False


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
    command_queue: CommandQueue | None = None,
) -> TaskPlan:
    """Execute a task plan with parallel dispatch and QA gates.

    Tasks are dispatched in dependency order. Independent tasks run in parallel
    up to config.safety.max_parallel workers.

    Returns the updated plan with task statuses and results.
    """
    # Pre-flight: validate provider is available before starting execution
    if not config.safety.dry_run:
        for role_config in (config.generator, config.evaluator):
            provider = create_provider(role_config)
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
                    if command_queue and command_queue.is_paused:
                        time.sleep(1)
                        continue

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
                        if command_queue and command_queue.pop_skip(task.id):
                            task.status = TaskStatus.SKIPPED
                            task.completed_at = datetime.now(timezone.utc).isoformat()
                            task.result = "Skipped by command queue"
                            hooks.emit(
                                Event(
                                    type=EventType.TASK_SKIPPED,
                                    task_id=task.id,
                                    module=task.module,
                                    data={
                                        "reason": task.result,
                                        "description": task.description,
                                    },
                                )
                            )
                            progress(
                                f"    [dim]Task {task.id} SKIPPED[/] ({task.module}): command queue"
                            )
                            continue

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
                            command_queue,
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
    command_queue: CommandQueue | None = None,
) -> int:
    """Execute a single task with dispatch, QA gates, and retry logic.

    Returns the number of dispatches made.
    """
    if module_semaphore:
        module_semaphore.acquire()

    try:
        return _execute_single_task_inner(
            task, config, logger, progress, detail, max_retries, hooks, command_queue
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
    command_queue: CommandQueue | None = None,
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
            command_queue,
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


def _resolve_working_dir(
    task: TaskSpec,
    config: OrchestratorConfig,
    worktree_path: Path | None,
) -> tuple[Path, Path]:
    """Resolve working_dir and module_dir (fixed for all retries)."""
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
    return working_dir, module_dir


def _prepare_task_prompt(
    task: TaskSpec,
    config: OrchestratorConfig,
    branch_name: str,
    worktree_path: Path | None,
    dispatches: int,
    progress: Callable[[str], None],
) -> None:
    """Backward-compatible no-op placeholder for older call sites."""
    del config, branch_name, worktree_path, dispatches, progress
    task.prompt = task.generator_prompt or task.prompt


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
    eval_result: EvalResult | str,
    max_retries: int,
    logger: ActionLogger,
    progress: Callable[[str], None],
    hooks: HookRegistry | None,
) -> bool:
    """Handle retry logic after QA failure. Returns True to continue loop, False to stop."""
    if isinstance(eval_result, EvalResult):
        retryable = eval_result.retryable
        feedback = eval_result.feedback
    else:
        failed_results = [r for r in task.qa_results if not r.passed]
        retryable = not failed_results or any(result.retryable for result in failed_results)
        feedback = EvalFeedback(summary="QA failed")

    if not retryable:
        task.status = TaskStatus.FAILED
        task.completed_at = datetime.now(timezone.utc).isoformat()
        progress(f"    [bold red]Task {task.id} FAILED[/] — evaluator marked result non-retryable")
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
    task.feedback_history.append(
        {
            "retry": task.retries,
            "summary": feedback.summary,
            "specific_errors": list(feedback.specific_errors),
            "files": list(feedback.files_to_check),
            "remediation": list(feedback.remediation_steps),
            "failed_criteria": list(feedback.failed_criteria),
            "evidence": feedback.evidence,
            "missing_behaviors": list(feedback.missing_behaviors),
        }
    )
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
    command_queue: CommandQueue | None = None,
) -> int:
    """Inner dispatch loop with retry logic. Extracted for worktree cleanup."""
    dispatches = 0
    working_dir, _module_dir = _resolve_working_dir(task, config, worktree_path)

    lc = config.lifecycle_hooks
    hook_cwd = worktree_path or config.root
    generator = GeneratorRunner(config.generator, config)
    evaluator = EvaluatorRunner(config.evaluator, config)
    feedback: EvalFeedback | None = None

    while True:
        hb = _HeartbeatTracker(task.id, task.module, progress, detail, hooks)

        progress("    [dim]Phase[/]: generating")
        if hooks:
            hooks.emit(
                Event(
                    type=EventType.PHASE_CHANGED,
                    task_id=task.id,
                    module=task.module,
                    data={"phase": "generating", "attempt": task.retries + 1},
                )
            )

        if lc.before_run:
            _run_lifecycle_hook("before_run", lc.before_run, hook_cwd, progress, lc.timeout)
        gen_output = generator.execute(
            task=task,
            worktree=working_dir,
            branch_name=branch_name,
            feedback=feedback,
            on_event=hb.on_event,
        )
        dispatches += 1
        task.result = gen_output.output
        task.cost_usd += gen_output.cost_usd
        logger.log_dispatch(
            task.module,
            (task.generator_prompt or task.prompt or task.description)[:200],
            {
                "success": gen_output.success,
                "duration": gen_output.duration_seconds,
                "exit_code": 0 if gen_output.success else 1,
                "event_count": gen_output.event_count,
                "last_tool_use": gen_output.last_tool,
                "cost_usd": gen_output.cost_usd,
            },
        )

        if gen_output.success and lc.after_run:
            _run_lifecycle_hook("after_run", lc.after_run, hook_cwd, progress, lc.timeout)

        if not gen_output.success:
            task.status = TaskStatus.FAILED
            task.completed_at = datetime.now(timezone.utc).isoformat()
            progress(f"    [red]GENERATOR FAILED[/]: {gen_output.output[:200]}")
            if hooks:
                hooks.emit(
                    Event(
                        type=EventType.TASK_FAILED,
                        task_id=task.id,
                        module=task.module,
                        data={"reason": "generator_failed", "description": task.description},
                    )
                )
            return dispatches

        progress(
            f"    [green]Dispatch completed[/] "
            f"({gen_output.duration_seconds}s, {gen_output.event_count} events)"
        )
        detail(f"    Output preview: {gen_output.output[:500]}")

        if task.skip_qa:
            _mark_completed(task, logger, progress, hooks)
            return dispatches

        _check_and_log_delivery(config.root, branch_name, logger, task, progress)
        _autofill_ci_params(task.qa_checks, branch_name, config, task.module)

        progress("    [dim]Phase[/]: evaluating")
        if hooks:
            hooks.emit(
                Event(
                    type=EventType.PHASE_CHANGED,
                    task_id=task.id,
                    module=task.module,
                    data={"phase": "evaluating", "attempt": task.retries + 1},
                )
            )

        eval_result = evaluator.evaluate(task, gen_output, working_dir)
        task.qa_results = list(eval_result.qa_results)
        task.cost_usd += eval_result.cost_usd
        progress(
            f"      [cyan]Evaluator score[/]: {eval_result.score} "
            f"({'pass' if eval_result.passed else 'fail'})"
        )
        if hooks:
            hooks.emit(
                Event(
                    type=EventType.EVAL_SCORED,
                    task_id=task.id,
                    module=task.module,
                    data={
                        "score": eval_result.score,
                        "passed": eval_result.passed,
                        "attempt": task.retries + 1,
                    },
                )
            )

        if command_queue and command_queue.pop_force_pass(task.id):
            progress("    [yellow]Command queue force-pass applied[/]")
            _mark_completed(task, logger, progress, hooks)
            return dispatches

        if eval_result.passed:
            _mark_completed(task, logger, progress, hooks)
            return dispatches

        feedback = eval_result.feedback
        if not _handle_retry(task, eval_result, max_retries, logger, progress, hooks):
            return dispatches


class Orchestrator:
    """Wrapper around the execution engine."""

    def __init__(self, config: OrchestratorConfig, command_queue: CommandQueue | None = None):
        self.config = config
        self.command_queue = command_queue

    def run(
        self,
        plan: TaskPlan,
        logger: ActionLogger,
        on_progress: Callable[[str], None] | None = None,
        verbose: bool = False,
        hooks: HookRegistry | None = None,
        command_queue: CommandQueue | None = None,
    ) -> TaskPlan:
        return execute_plan(
            plan,
            self.config,
            logger,
            on_progress=on_progress,
            verbose=verbose,
            hooks=hooks,
            command_queue=command_queue or self.command_queue,
        )
