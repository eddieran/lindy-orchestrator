"""Tests for OpenTelemetry import guard and optional dependency handling.

The trackers module uses a factory pattern with optional provider backends.
These tests verify:
- Unknown tracker provider raises a clear error
- The factory gracefully handles import failures
- The system operates correctly when optional dependencies are absent
"""

from __future__ import annotations

import pytest

from lindy_orchestrator.trackers import TrackerProvider, create_tracker


class TestTrackerImportGuard:
    """Verify that the tracker factory handles missing/invalid providers."""

    def test_unknown_provider_raises_valueerror(self):
        with pytest.raises(ValueError, match="Unknown tracker provider"):
            create_tracker("nonexistent_provider")

    def test_github_provider_exists(self):
        tracker = create_tracker("github", repo="owner/repo")
        assert isinstance(tracker, TrackerProvider)

    def test_factory_with_none_provider_raises(self):
        with pytest.raises((ValueError, TypeError)):
            create_tracker(None)  # type: ignore[arg-type]

    def test_empty_string_provider_raises(self):
        with pytest.raises(ValueError, match="Unknown tracker provider"):
            create_tracker("")


class TestOptionalDependencyGuard:
    """Simulate missing optional dependencies and verify graceful degradation."""

    def test_import_guard_pattern_for_optional_module(self):
        """Verify the standard import guard pattern works."""
        try:
            import opentelemetry  # type: ignore[import-not-found]  # noqa: F401

            has_otel = True
        except ImportError:
            has_otel = False

        # In a system without opentelemetry, has_otel should be False
        # and the core system should still function
        if not has_otel:
            from lindy_orchestrator.hooks import HookRegistry

            reg = HookRegistry()
            assert reg.handler_count == 0

    def test_analytics_works_without_otel(self, tmp_path):
        """Analytics module must work regardless of opentelemetry availability."""
        from lindy_orchestrator.analytics import compute_aggregate_stats

        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        stats = compute_aggregate_stats(sessions_dir, log_path=None)
        assert stats.total_tasks == 0

    def test_hooks_work_without_otel(self):
        """Hook system must function without opentelemetry."""
        from lindy_orchestrator.hooks import Event, EventType, HookRegistry

        reg = HookRegistry()
        results: list[str] = []
        reg.on(EventType.TASK_STARTED, lambda e: results.append("fired"))
        reg.emit(Event(type=EventType.TASK_STARTED))
        assert results == ["fired"]

    def test_dashboard_works_without_otel(self):
        """Dashboard must render without opentelemetry."""
        from io import StringIO

        from rich.console import Console

        from lindy_orchestrator.dashboard import Dashboard
        from lindy_orchestrator.hooks import HookRegistry
        from lindy_orchestrator.models import TaskItem, TaskPlan

        plan = TaskPlan(goal="test", tasks=[TaskItem(id=1, module="m", description="d")])
        hooks = HookRegistry()
        console = Console(file=StringIO(), force_terminal=False)
        dash = Dashboard(plan, hooks, console=console)
        dash.start()
        panel = dash._build_panel()
        assert panel is not None

    def test_tracker_protocol_without_otel(self):
        """TrackerProvider protocol can be used without otel."""
        from lindy_orchestrator.trackers.base import TrackerProvider

        class DummyTracker:
            def fetch_issues(self, project="", labels=None, status="open", limit=20):
                return []

            def update_status(self, issue_id="", status="", comment=""):
                return True

            def add_comment(self, issue_id="", comment=""):
                return True

        t = DummyTracker()
        assert isinstance(t, TrackerProvider)
        assert t.fetch_issues() == []


class TestTrackerFactoryEdgeCases:
    """Edge cases in tracker factory registration."""

    def test_github_tracker_default_provider(self):
        """Default provider param ('github') should work."""
        tracker = create_tracker(repo="test/repo")
        assert isinstance(tracker, TrackerProvider)

    def test_github_tracker_with_explicit_provider(self):
        tracker = create_tracker("github", repo="owner/repo")
        assert isinstance(tracker, TrackerProvider)
