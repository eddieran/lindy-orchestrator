"""Tests for the web dashboard server."""

from __future__ import annotations

import json
import queue
import urllib.request
import urllib.error

import pytest

from lindy_orchestrator.hooks import Event, EventType, HookRegistry
from lindy_orchestrator.models import TaskItem, TaskPlan, TaskStatus
from lindy_orchestrator.web.server import (
    SSEManager,
    WebDashboard,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_plan() -> TaskPlan:
    return TaskPlan(
        goal="Test goal",
        tasks=[
            TaskItem(id=1, module="mod-a", description="First task", status=TaskStatus.COMPLETED),
            TaskItem(
                id=2,
                module="mod-b",
                description="Second task",
                depends_on=[1],
                status=TaskStatus.PENDING,
            ),
        ],
    )


# ---------------------------------------------------------------------------
# SSEManager tests
# ---------------------------------------------------------------------------


class TestSSEManager:
    def test_add_remove_client(self) -> None:
        mgr = SSEManager()
        assert mgr.client_count == 0

        q = mgr.add_client()
        assert mgr.client_count == 1

        mgr.remove_client(q)
        assert mgr.client_count == 0

    def test_remove_unknown_client_no_error(self) -> None:
        mgr = SSEManager()
        q: queue.Queue[str | None] = queue.Queue()
        mgr.remove_client(q)  # should not raise

    def test_broadcast_delivers_to_clients(self) -> None:
        mgr = SSEManager()
        q1 = mgr.add_client()
        q2 = mgr.add_client()

        mgr.broadcast("test_event", {"key": "value"})

        msg1 = q1.get_nowait()
        msg2 = q2.get_nowait()
        assert msg1 == msg2
        assert "event: test_event" in msg1
        assert '"key": "value"' in msg1

    def test_broadcast_no_clients_no_error(self) -> None:
        mgr = SSEManager()
        mgr.broadcast("test", {"data": 1})  # should not raise

    def test_broadcast_skips_full_queue(self) -> None:
        """A full client queue should not block broadcast."""
        mgr = SSEManager()
        q = mgr.add_client()
        # Fill the queue
        for _ in range(10000):
            try:
                q.put_nowait("filler")
            except queue.Full:
                break
        # Should not raise even with full queue
        mgr.broadcast("test", {"data": 1})


# ---------------------------------------------------------------------------
# WebDashboard lifecycle tests
# ---------------------------------------------------------------------------


class TestWebDashboard:
    def test_start_stop_lifecycle(self) -> None:
        plan = _make_plan()
        hooks = HookRegistry()
        dashboard = WebDashboard(plan, hooks, port=18420)

        url = dashboard.start()
        assert url.startswith("http://")
        assert dashboard.port >= 18420

        # Verify server responds
        resp = urllib.request.urlopen(f"{url}/api/state", timeout=2)
        assert resp.status == 200
        data = json.loads(resp.read())
        assert data["goal"] == "Test goal"
        assert len(data["tasks"]) == 2

        dashboard.stop()

    def test_port_fallback(self) -> None:
        """If the preferred port is taken, it should try the next ports."""
        plan = _make_plan()
        hooks = HookRegistry()

        # Start first dashboard to occupy port
        d1 = WebDashboard(plan, hooks, port=18430)
        d1.start()

        # Second dashboard should pick next port
        d2 = WebDashboard(plan, hooks, port=18430)
        d2.start()
        assert d2.port > 18430

        d2.stop()
        d1.stop()

    def test_hook_events_broadcast_to_sse(self) -> None:
        plan = _make_plan()
        hooks = HookRegistry()
        dashboard = WebDashboard(plan, hooks, port=18440)
        dashboard.start()

        # Connect SSE client directly via SSEManager
        client_q = dashboard._sse.add_client()

        # Emit a hook event
        hooks.emit(Event(type=EventType.TASK_STARTED, task_id=1, module="mod-a"))

        # Should receive the broadcast
        msg = client_q.get(timeout=2)
        assert "task_event" in msg
        assert "task_started" in msg

        dashboard._sse.remove_client(client_q)
        dashboard.stop()

    def test_stop_unsubscribes_hooks(self) -> None:
        plan = _make_plan()
        hooks = HookRegistry()
        dashboard = WebDashboard(plan, hooks, port=18450)
        dashboard.start()
        assert hooks.handler_count > 0

        dashboard.stop()
        assert hooks.handler_count == 0


# ---------------------------------------------------------------------------
# HTTP route tests
# ---------------------------------------------------------------------------


class TestDashboardRoutes:
    @pytest.fixture(autouse=True)
    def _setup(self) -> None:
        self.plan = _make_plan()
        self.hooks = HookRegistry()
        self.dashboard = WebDashboard(self.plan, self.hooks, port=18460)
        self.url = self.dashboard.start()
        yield  # type: ignore[misc]
        self.dashboard.stop()

    def test_index_returns_html(self) -> None:
        resp = urllib.request.urlopen(f"{self.url}/", timeout=2)
        assert resp.status == 200
        body = resp.read().decode()
        assert "<!DOCTYPE html>" in body
        assert "Lindy Orchestrator" in body

    def test_api_state_returns_json(self) -> None:
        resp = urllib.request.urlopen(f"{self.url}/api/state", timeout=2)
        assert resp.status == 200
        data = json.loads(resp.read())
        assert data["goal"] == "Test goal"

    def test_api_metrics_returns_json(self) -> None:
        resp = urllib.request.urlopen(f"{self.url}/api/metrics", timeout=2)
        assert resp.status == 200
        data = json.loads(resp.read())
        assert "task_counts" in data
        assert data["total_tasks"] == 2

    def test_404_for_unknown_path(self) -> None:
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(f"{self.url}/nonexistent", timeout=2)
        assert exc_info.value.code == 404

    def test_metrics_with_custom_fn(self) -> None:
        self.dashboard.stop()

        def custom() -> dict:
            return {"custom": True}

        d = WebDashboard(self.plan, self.hooks, metrics=custom, port=18470)
        url = d.start()
        resp = urllib.request.urlopen(f"{url}/api/metrics", timeout=2)
        data = json.loads(resp.read())
        assert data["custom"] is True
        d.stop()
