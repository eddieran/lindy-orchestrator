"""Tests for garbage collection."""

import json
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch

from lindy_orchestrator.config import (
    LoggingConfig,
    ModuleConfig,
    OrchestratorConfig,
    ProjectConfig,
)
from lindy_orchestrator.gc import (
    GCReport,
    _check_log_rotation,
    _check_status_drift,
    _find_old_sessions,
    _find_orphan_plans,
    format_gc_report,
    run_gc,
)


def _make_config(tmp_path: Path) -> OrchestratorConfig:
    cfg = OrchestratorConfig(
        project=ProjectConfig(name="test", branch_prefix="af"),
        modules=[ModuleConfig(name="backend", path="backend")],
        logging=LoggingConfig(
            dir=".orchestrator/logs",
            session_dir=".orchestrator/sessions",
            log_file="actions.jsonl",
        ),
    )
    cfg._config_dir = tmp_path

    # Create required dirs
    (tmp_path / ".orchestrator" / "logs").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".orchestrator" / "sessions").mkdir(parents=True, exist_ok=True)
    (tmp_path / "backend").mkdir(exist_ok=True)

    return cfg


class TestOldSessions:
    def test_finds_old_sessions(self, tmp_path: Path):
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()

        # Create an old session
        old_date = (datetime.now(timezone.utc) - timedelta(days=45)).isoformat()
        session_file = sessions_dir / "old-session.json"
        session_file.write_text(
            json.dumps({"session_id": "old", "started_at": old_date, "status": "completed"})
        )

        actions = _find_old_sessions(sessions_dir, max_age_days=30, apply=False)
        assert len(actions) == 1
        assert actions[0].category == "old_session"
        assert "45" in actions[0].description or "old" in actions[0].description

    def test_keeps_recent_sessions(self, tmp_path: Path):
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()

        recent_date = datetime.now(timezone.utc).isoformat()
        session_file = sessions_dir / "recent.json"
        session_file.write_text(
            json.dumps({"session_id": "recent", "started_at": recent_date, "status": "completed"})
        )

        actions = _find_old_sessions(sessions_dir, max_age_days=30, apply=False)
        assert len(actions) == 0

    def test_archives_when_applied(self, tmp_path: Path):
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()

        old_date = (datetime.now(timezone.utc) - timedelta(days=45)).isoformat()
        session_file = sessions_dir / "old.json"
        session_file.write_text(
            json.dumps({"session_id": "old", "started_at": old_date, "status": "done"})
        )

        actions = _find_old_sessions(sessions_dir, max_age_days=30, apply=True)
        assert len(actions) == 1
        assert actions[0].applied
        assert not session_file.exists()
        assert (sessions_dir / "archive" / "old.json").exists()


class TestLogRotation:
    def test_detects_oversized_log(self, tmp_path: Path):
        log_file = tmp_path / "actions.jsonl"
        # Write >10MB
        log_file.write_text("x" * (11 * 1024 * 1024))

        actions = _check_log_rotation(log_file, max_size_mb=10, apply=False)
        assert len(actions) == 1
        assert actions[0].category == "log_rotation"
        assert "11" in actions[0].description

    def test_keeps_small_log(self, tmp_path: Path):
        log_file = tmp_path / "actions.jsonl"
        log_file.write_text("small log\n")

        actions = _check_log_rotation(log_file, max_size_mb=10, apply=False)
        assert len(actions) == 0

    def test_rotates_when_applied(self, tmp_path: Path):
        log_file = tmp_path / "actions.jsonl"
        log_file.write_text("x" * (11 * 1024 * 1024))

        actions = _check_log_rotation(log_file, max_size_mb=10, apply=True)
        assert len(actions) == 1
        assert actions[0].applied
        # Original file should be fresh (empty)
        assert log_file.exists()
        assert log_file.stat().st_size == 0
        # Archive should exist
        archives = list(tmp_path.glob("actions-*.jsonl"))
        assert len(archives) == 1


class TestStatusDrift:
    def test_detects_stale_status(self, tmp_path: Path):
        cfg = _make_config(tmp_path)

        # Create a status file and backdate it
        status_dir = tmp_path / ".orchestrator" / "status"
        status_dir.mkdir(parents=True, exist_ok=True)
        status_file = status_dir / "backend.md"
        status_file.write_text("# STATUS\n")
        # Set mtime to 10 days ago
        old_time = time.time() - (10 * 86400)
        import os

        os.utime(status_file, (old_time, old_time))

        actions = _check_status_drift(cfg, stale_days=7)
        assert len(actions) == 1
        assert actions[0].category == "status_drift"
        assert "backend" in actions[0].description

    def test_fresh_status_ok(self, tmp_path: Path):
        cfg = _make_config(tmp_path)

        status_dir = tmp_path / ".orchestrator" / "status"
        status_dir.mkdir(parents=True, exist_ok=True)
        status_file = status_dir / "backend.md"
        status_file.write_text("# STATUS\n")
        # mtime is now (fresh)

        actions = _check_status_drift(cfg, stale_days=7)
        assert len(actions) == 0


class TestOrphanPlans:
    def test_detects_old_orphan_plans(self, tmp_path: Path):
        plans_dir = tmp_path / ".orchestrator" / "plans"
        plans_dir.mkdir(parents=True)
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()

        # Create an old plan file
        plan_file = plans_dir / "old-plan.json"
        plan_file.write_text(json.dumps({"goal": "old goal"}))
        # Backdate it
        old_time = time.time() - (45 * 86400)
        import os

        os.utime(plan_file, (old_time, old_time))

        actions = _find_orphan_plans(tmp_path, sessions_dir)
        assert len(actions) == 1
        assert actions[0].category == "orphan_plan"


class TestFormatReport:
    def test_clean_workspace(self):
        report = GCReport()
        result = format_gc_report(report)
        assert "clean" in result.lower()

    def test_dry_run_report(self):
        from lindy_orchestrator.gc import GCAction

        report = GCReport(
            actions=[
                GCAction(
                    category="stale_branch",
                    description="Branch af/task-1 is 20 days old",
                ),
                GCAction(
                    category="old_session",
                    description="Session abc is 45 days old",
                ),
            ],
            dry_run=True,
        )
        result = format_gc_report(report)
        assert "DRY RUN" in result
        assert "2 action(s)" in result
        assert "would apply" in result
        assert "Run with --apply" in result


class TestRunGC:
    @patch("lindy_orchestrator.gc.subprocess.run")
    def test_dry_run_no_side_effects(self, mock_run, tmp_path: Path):
        # Mock git commands to return empty
        from unittest.mock import MagicMock

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        mock_run.return_value = mock_result

        cfg = _make_config(tmp_path)

        # Create log and session
        log_file = cfg.log_path
        log_file.parent.mkdir(parents=True, exist_ok=True)
        log_file.write_text("small log\n")

        report = run_gc(cfg, apply=False)
        assert report.dry_run
        # No actions should have been applied
        for a in report.actions:
            assert not a.applied
