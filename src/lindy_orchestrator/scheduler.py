"""DAG-based parallel task execution with retry logic.

Executes tasks from a TaskPlan in dependency order, dispatching independent
tasks in parallel using concurrent.futures.
"""

from __future__ import annotations

import concurrent.futures
from typing import Callable

from .config import OrchestratorConfig
from .dispatcher import dispatch_agent
from .logger import ActionLogger
from .models import TaskItem, TaskPlan, TaskStatus
from .qa import run_qa_gate


def execute_plan(
    plan: TaskPlan,
    config: OrchestratorConfig,
    logger: ActionLogger,
    on_progress: Callable[[str], None] | None = None,
    verbose: bool = False,
) -> TaskPlan:
    """Execute a task plan with parallel dispatch and QA gates.

    Tasks are dispatched in dependency order. Independent tasks run in parallel
    up to config.safety.max_parallel workers.

    Returns the updated plan with task statuses and results.
    """
    max_retries = config.safety.max_retries_per_task
    max_parallel = config.safety.max_parallel
    total_dispatches = 0

    def progress(msg: str) -> None:
        if on_progress:
            on_progress(msg)

    def detail(msg: str) -> None:
        if on_progress and verbose:
            on_progress(msg)

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_parallel) as pool:
        while not plan.is_complete() and not plan.has_failures():
            ready = plan.next_ready()
            if not ready:
                break

            if len(ready) > 1:
                progress(f"\n  [bold]Dispatching {len(ready)} tasks in parallel...[/]")

            # Submit all ready tasks
            futures: dict[concurrent.futures.Future, TaskItem] = {}
            for task in ready:
                task.status = TaskStatus.IN_PROGRESS
                progress(f"\n  [bold]Task {task.id}:[/] [{task.module}] {task.description}")

                if config.safety.dry_run:
                    task.status = TaskStatus.COMPLETED
                    task.result = "[DRY RUN] Skipped"
                    progress(f"    [yellow]DRY RUN[/] — would dispatch to {task.module}")
                    continue

                future = pool.submit(
                    _execute_single_task,
                    task,
                    config,
                    logger,
                    progress,
                    detail,
                    max_retries,
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

    return plan


def _execute_single_task(
    task: TaskItem,
    config: OrchestratorConfig,
    logger: ActionLogger,
    progress: Callable[[str], None],
    detail: Callable[[str], None],
    max_retries: int,
) -> int:
    """Execute a single task with dispatch, QA gates, and retry logic.

    Returns the number of dispatches made.
    """
    dispatches = 0

    while True:
        # Dispatch to module agent
        progress(f"    Dispatching to [bold]{task.module}[/] agent...")
        working_dir = config.module_path(task.module)

        def _on_event(event: dict) -> None:
            """Log tool use events in verbose mode."""
            tool = event.get("message", {}).get("content", [{}])
            if isinstance(tool, list):
                for block in tool:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        detail(f"      [dim]tool: {block.get('name', '?')}[/]")

        result = dispatch_agent(
            module=task.module,
            working_dir=working_dir,
            prompt=task.prompt,
            config=config.dispatcher,
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
            return dispatches

        progress(
            f"    [green]Dispatch completed[/] "
            f"({result.duration_seconds}s, {result.event_count} events)"
        )
        detail(f"    Output preview: {result.output[:500]}")

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
            else:
                progress(f"      [red]FAIL[/]: {qa_result.output[:200]}")
                detail(f"      Full output: {qa_result.output}")
                all_qa_passed = False

        if all_qa_passed:
            task.status = TaskStatus.COMPLETED
            progress(f"    [bold green]Task {task.id} COMPLETED[/]")
            logger.log_action(
                "task_completed",
                details={
                    "task_id": task.id,
                    "module": task.module,
                    "description": task.description,
                },
            )
            return dispatches

        # Retry with QA feedback
        task.retries += 1
        if task.retries > max_retries:
            task.status = TaskStatus.FAILED
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
            return dispatches

        # Augment prompt with failure feedback
        failed_checks = [r for r in task.qa_results if not r.passed]
        failure_detail = "\n".join(f"- {r.gate}: {r.output[:300]}" for r in failed_checks)
        task.prompt = (
            f"{task.prompt}\n\n"
            f"## IMPORTANT: Previous attempt failed QA verification\n"
            f"The following quality checks failed:\n{failure_detail}\n\n"
            f"Fix these issues and try again.\n"
        )
        task.qa_results = []
        progress(
            f"    [yellow]QA failed, retrying with feedback[/] ({task.retries}/{max_retries})..."
        )
