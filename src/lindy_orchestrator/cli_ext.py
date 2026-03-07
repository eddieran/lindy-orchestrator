"""Extension commands for lindy-orchestrate (gc, scan, validate, issues, etc.)."""

from __future__ import annotations

import json
import time
from typing import Any, Optional

import typer
from rich.console import Console

from .cli_helpers import finalise_session, make_on_progress
from .dispatcher import find_claude_cli
from .logger import ActionLogger
from .reporter import PlanProgress, print_goal_report
from .session import SessionManager
from .status.parser import parse_status_md


def register_ext_commands(app: typer.Typer, console: Console, helpers: dict[str, Any]) -> None:
    """Register extension commands on the Typer app.

    Args:
        helpers: dict with keys 'load_cfg', 'plan_to_dict', 'plan_from_dict',
                 'persist_plan', 'resolve_goal'.
    """
    _load_cfg = helpers["load_cfg"]
    _plan_to_dict = helpers["plan_to_dict"]
    _plan_from_dict = helpers["plan_from_dict"]
    _persist_plan = helpers["persist_plan"]

    # -------------------------------------------------------------------
    # gc
    # -------------------------------------------------------------------

    @app.command()
    def gc(
        config: Optional[str] = typer.Option(None, "-c", "--config"),
        apply: bool = typer.Option(
            False, "--apply", help="Actually perform cleanup (default: dry run)"
        ),
        branch_age: int = typer.Option(14, "--branch-age", help="Max age for task branches (days)"),
        session_age: int = typer.Option(30, "--session-age", help="Max age for sessions (days)"),
        log_size: int = typer.Option(10, "--log-size", help="Max log file size (MB)"),
        status_stale: int = typer.Option(
            7, "--status-stale", help="STATUS.md stale threshold (days)"
        ),
    ):
        """Clean up stale branches, old sessions, oversized logs, and orphan plans.

        Runs in dry-run mode by default. Use --apply to execute cleanup.
        """
        from .gc import format_gc_report, run_gc

        cfg = _load_cfg(config)
        mode = "[red]APPLY[/]" if apply else "[yellow]DRY RUN[/]"
        console.print(f"[bold]lindy-orchestrate gc[/] — {mode}\n")

        report = run_gc(
            cfg,
            apply=apply,
            max_branch_age_days=branch_age,
            max_session_age_days=session_age,
            max_log_size_mb=log_size,
            status_stale_days=status_stale,
        )

        console.print(format_gc_report(report))

        if not report.actions:
            console.print("[bold green]Workspace is clean.[/]")
        elif not apply:
            console.print(
                f"\n[yellow]{report.action_count} action(s) found.[/] Run with --apply to execute."
            )

    # -------------------------------------------------------------------
    # scan
    # -------------------------------------------------------------------

    @app.command()
    def scan(
        config: Optional[str] = typer.Option(None, "-c", "--config"),
        module: Optional[str] = typer.Option(None, "--module", help="Scan specific module"),
        grade_only: bool = typer.Option(False, "--grade-only", help="Only show grades"),
    ):
        """Scan for entropy: architecture drift, contract violations, quality decay."""
        from .entropy.scanner import format_scan_report, run_scan

        cfg = _load_cfg(config)

        console.print("[bold]lindy-orchestrate scan[/] — Entropy Scanner\n")

        report = run_scan(cfg, module_filter=module)

        output = format_scan_report(report, grade_only=grade_only)
        console.print(output)

        error_count = len([f for f in report.findings if f.severity == "error"])
        warning_count = len([f for f in report.findings if f.severity == "warning"])

        if error_count:
            console.print(f"\n[red]{error_count} error(s), {warning_count} warning(s)[/]")
        elif warning_count:
            console.print(f"\n[yellow]{warning_count} warning(s)[/]")
        else:
            console.print("\n[bold green]No issues found.[/]")

    # -------------------------------------------------------------------
    # validate
    # -------------------------------------------------------------------

    @app.command()
    def validate(
        config: Optional[str] = typer.Option(None, "-c", "--config"),
    ):
        """Validate config and STATUS.md files."""
        try:
            cfg = _load_cfg(config)
        except Exception as e:
            console.print(f"[red]Config error: {e}[/]")
            raise typer.Exit(1)

        console.print(f"[green]Config valid[/]: {len(cfg.modules)} modules")

        errors = 0
        for mod in cfg.modules:
            mod_path = cfg.module_path(mod.name)
            if not mod_path.exists():
                console.print(f"  [red]Module path missing: {mod_path}[/]")
                errors += 1
            else:
                console.print(f"  [green]{mod.name}[/]: {mod_path}")

            status_path = cfg.status_path(mod.name)
            if not status_path.exists():
                console.print(f"    [yellow]STATUS.md missing: {status_path}[/]")
            else:
                s = parse_status_md(status_path)
                console.print(
                    f"    STATUS.md: health={s.meta.overall_health}, "
                    f"active={len(s.active_work)}, blockers={len(s.blockers)}"
                )

        # Check claude CLI
        if find_claude_cli():
            console.print("[green]Claude CLI found[/]")
        else:
            console.print("[yellow]Claude CLI not found in PATH[/]")

        if errors:
            console.print(f"\n[red]{errors} error(s) found.[/]")
            raise typer.Exit(1)
        else:
            console.print("\n[bold green]All checks passed.[/]")

    # -------------------------------------------------------------------
    # issues
    # -------------------------------------------------------------------

    @app.command()
    def issues(
        config: Optional[str] = typer.Option(None, "-c", "--config"),
        label: Optional[str] = typer.Option(None, "--label", help="Filter by label"),
        status: str = typer.Option("open", "--status", help="Issue status filter"),
        limit: int = typer.Option(20, "--limit", "-n", help="Max issues to fetch"),
        as_json: bool = typer.Option(False, "--json", help="Output as JSON"),
    ):
        """List issues from the configured tracker."""
        from .trackers import create_tracker

        cfg = _load_cfg(config)

        if not cfg.tracker.enabled:
            console.print("[yellow]Tracker is disabled.[/] Set tracker.enabled: true in config.")
            return

        tracker = create_tracker(cfg.tracker.provider, repo=cfg.tracker.repo)
        labels = [label] if label else (cfg.tracker.labels or None)

        try:
            issue_list = tracker.fetch_issues(
                project=cfg.project.name,
                labels=labels,
                status=status,
                limit=limit,
            )
        except Exception as e:
            console.print(f"[red]Failed to fetch issues: {e}[/]")
            raise typer.Exit(1)

        if as_json:
            from dataclasses import asdict

            console.print_json(json.dumps([asdict(i) for i in issue_list], indent=2))
            return

        if not issue_list:
            console.print("[dim]No issues found.[/]")
            return

        console.print(f"[bold]{len(issue_list)} issue(s):[/]\n")
        for issue in issue_list:
            labels_str = f" [{', '.join(issue.labels)}]" if issue.labels else ""
            console.print(f"  [bold]#{issue.id}[/]{labels_str} {issue.title}")
            if issue.url:
                console.print(f"    [dim]{issue.url}[/]")

    # -------------------------------------------------------------------
    # run-issue
    # -------------------------------------------------------------------

    @app.command(name="run-issue")
    def run_issue(
        issue_id: str = typer.Argument(..., help="Issue ID to execute"),
        config: Optional[str] = typer.Option(None, "-c", "--config"),
        dry_run: bool = typer.Option(False, "--dry-run", help="Plan only, don't execute"),
        verbose: bool = typer.Option(False, "-v", "--verbose"),
    ):
        """Fetch an issue from the tracker and execute it as a goal."""
        from .planner import generate_plan
        from .scheduler import execute_plan
        from .trackers import create_tracker

        cfg = _load_cfg(config)

        if not cfg.tracker.enabled:
            console.print("[yellow]Tracker is disabled.[/] Set tracker.enabled: true in config.")
            raise typer.Exit(1)

        tracker = create_tracker(cfg.tracker.provider, repo=cfg.tracker.repo)

        try:
            all_issues = tracker.fetch_issues(project=cfg.project.name, status="open", limit=100)
        except Exception as e:
            console.print(f"[red]Failed to fetch issues: {e}[/]")
            raise typer.Exit(1)

        issue = next((i for i in all_issues if i.id == issue_id), None)
        if not issue:
            console.print(f"[red]Issue #{issue_id} not found or not open.[/]")
            raise typer.Exit(1)

        goal = f"{issue.title}\n\n{issue.body}" if issue.body else issue.title
        console.print(f"[bold]Issue #{issue.id}:[/] {issue.title}")
        console.print(f"[dim]{issue.url}[/]\n")

        if dry_run:
            cfg.safety.dry_run = True

        if not find_claude_cli():
            console.print("[red]Error: Claude CLI not found in PATH.[/]")
            raise typer.Exit(1)

        logger = ActionLogger(cfg.log_path)
        sessions = SessionManager(cfg.sessions_path)
        session = sessions.create(goal=f"[Issue #{issue.id}] {issue.title}")
        console.print(f"Session: {session.session_id}\n")

        start = time.monotonic()
        on_progress = make_on_progress(console)

        logger.log_action(
            "session_start",
            details={"goal": goal, "issue_id": issue.id, "dry_run": cfg.safety.dry_run},
        )
        console.print("[bold cyan][1/3][/] Generating task plan from issue...")
        progress = PlanProgress(console=console)
        progress.start()

        try:
            plan_result = generate_plan(goal, cfg, on_progress=on_progress, progress=progress)
        except Exception as e:
            progress.stop(f"Planning failed: {e}")
            console.print(f"[red]Planning failed: {e}[/]")
            session.status = "failed"
            sessions.save(session)
            raise typer.Exit(1)
        finally:
            if progress._live is not None:
                progress.stop()

        session.plan_json = _plan_to_dict(plan_result)
        sessions.save(session)
        _persist_plan(cfg.root, plan_result)

        console.print(f"\n  [bold]{len(plan_result.tasks)} tasks planned:[/]")
        for t in plan_result.tasks:
            deps = f" [dim](depends on: {t.depends_on})[/]" if t.depends_on else ""
            console.print(f"    {t.id}. [bold][{t.module}][/] {t.description}{deps}")

        console.print("\n[bold cyan][2/3][/] Executing tasks...")
        plan_result = execute_plan(
            plan_result, cfg, logger, on_progress=on_progress, verbose=verbose
        )

        console.print("\n[bold cyan][3/3][/] Generating report...")
        duration = round(time.monotonic() - start, 1)

        completed = [t for t in plan_result.tasks if t.status.value == "completed"]
        failed = [t for t in plan_result.tasks if t.status.value == "failed"]

        print_goal_report(
            f"{'GOAL COMPLETED' if not failed else 'GOAL PAUSED'}: {issue.title}\n\n"
            f"Completed: {len(completed)}/{len(plan_result.tasks)} tasks",
            dispatches=len(plan_result.tasks),
            duration=duration,
        )

        # Sync back to tracker
        if cfg.tracker.sync_on_complete and not cfg.safety.dry_run:
            task_summary = "\n".join(
                f"- [{t.module}] {t.description}: {t.status.value}" for t in plan_result.tasks
            )
            comment = (
                f"**lindy-orchestrator** completed execution.\n\n"
                f"**Result:** "
                f"{'All tasks completed' if not failed else f'{len(failed)} task(s) failed'}\n"
                f"**Duration:** {duration}s\n\n"
                f"### Tasks\n{task_summary}"
            )
            try:
                tracker.add_comment(issue.id, comment)
                if not failed:
                    tracker.update_status(issue.id, "closed")
                    console.print(f"[green]Issue #{issue.id} closed with summary.[/]")
                else:
                    console.print(f"[yellow]Comment added to issue #{issue.id}.[/]")
            except Exception as e:
                console.print(f"[yellow]Failed to sync to tracker: {e}[/]")

        completed, failed = finalise_session(session, sessions, plan_result)

        logger.log_action(
            "session_end",
            details={
                "duration_seconds": duration,
                "completed": len(completed),
                "failed": len(failed),
                "issue_id": issue.id,
            },
        )

    # -------------------------------------------------------------------
    # mailbox
    # -------------------------------------------------------------------

    @app.command()
    def mailbox(
        module: Optional[str] = typer.Argument(None, help="Module name to view messages for"),
        send_to: Optional[str] = typer.Option(None, "--send-to", help="Send a message to a module"),
        send_from: Optional[str] = typer.Option(None, "--send-from", help="Sender module name"),
        message: Optional[str] = typer.Option(None, "--message", "-m", help="Message content"),
        priority: str = typer.Option("normal", "--priority", "-p", help="Message priority"),
        config: Optional[str] = typer.Option(None, "-c", "--config"),
        as_json: bool = typer.Option(False, "--json", help="Output as JSON"),
    ):
        """View or send inter-agent mailbox messages.

        Examples:
          lindy-orchestrate mailbox frontend
          lindy-orchestrate mailbox --send-to backend --send-from frontend -m "Need API"
        """
        from .mailbox import Mailbox, Message

        cfg = _load_cfg(config)

        if not cfg.mailbox.enabled:
            console.print("[yellow]Mailbox is disabled.[/] Set mailbox.enabled: true in config.")
            return

        mb = Mailbox(cfg.root / cfg.mailbox.dir)

        # Send mode
        if send_to and message:
            from_mod = send_from or "cli"
            msg = Message(
                from_module=from_mod,
                to_module=send_to,
                content=message,
                priority=priority,
            )
            mb.send(msg)
            console.print(
                f"[green]Sent[/] message to [bold]{send_to}[/] from [bold]{from_mod}[/]: {message}"
            )
            return

        if send_to and not message:
            console.print("[red]--message is required when using --send-to[/]")
            raise typer.Exit(1)

        # View mode
        if not module:
            console.print("[bold]Mailbox Summary[/]\n")
            for mod in cfg.modules:
                count = mb.pending_count(mod.name)
                if count > 0:
                    console.print(f"  [bold]{mod.name}[/]: {count} pending message(s)")
                else:
                    console.print(f"  [dim]{mod.name}[/]: no pending messages")
            return

        # Show messages for specific module
        messages = mb.receive(module, unread_only=True)
        if as_json:
            import json as _json
            from dataclasses import asdict

            console.print_json(_json.dumps([asdict(m) for m in messages], indent=2, default=str))
            return

        if not messages:
            console.print(f"[dim]No pending messages for {module}.[/]")
            return

        console.print(f"[bold]Pending messages for {module}[/] ({len(messages)}):\n")
        for msg in messages:
            priority_tag = f" [{msg.priority.upper()}]" if msg.priority != "normal" else ""
            console.print(
                f"  [bold]{msg.from_module}[/]{priority_tag} ({msg.message_type}): {msg.content}"
            )
            console.print(f"    [dim]id={msg.id} at {msg.created_at}[/]")
