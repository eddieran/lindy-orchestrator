"""Tests for the SSE-driven web dashboard."""

from __future__ import annotations

import json
import queue
import time
import urllib.request

from lindy_orchestrator.hooks import Event, EventType, HookRegistry
from lindy_orchestrator.models import TaskItem, TaskPlan, TaskStatus
from lindy_orchestrator.web.server import SSEManager, WebDashboard


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _task(tid: int, module: str = "mod", desc: str = "do thing", **kw) -> TaskItem:
    return TaskItem(id=tid, module=module, description=desc, **kw)


def _plan(*tasks: TaskItem, goal: str = "test") -> TaskPlan:
    return TaskPlan(goal=goal, tasks=list(tasks))


def _simple_plan() -> TaskPlan:
    return _plan(
        _task(1, status=TaskStatus.COMPLETED, cost_usd=0.50),
        _task(2, depends_on=[1], status=TaskStatus.PENDING),
        goal="Test web dashboard",
    )


# ---------------------------------------------------------------------------
# SSEManager tests
# ---------------------------------------------------------------------------


class TestSSEManager:
    def test_add_and_remove_client(self):
        mgr = SSEManager()
        assert mgr.client_count == 0

        q = mgr.add_client()
        assert mgr.client_count == 1

        mgr.remove_client(q)
        assert mgr.client_count == 0

    def test_remove_nonexistent_client(self):
        """Removing a client not in the list should not raise."""
        mgr = SSEManager()
        q: queue.Queue[str] = queue.Queue()
        mgr.remove_client(q)  # Should not raise

    def test_broadcast_to_clients(self):
        mgr = SSEManager()
        q1 = mgr.add_client()
        q2 = mgr.add_client()

        mgr.broadcast("task_started", {"task_id": 1})

        msg1 = q1.get_nowait()
        msg2 = q2.get_nowait()
        assert msg1 == msg2
        assert "event: task_started" in msg1
        assert '"task_id": 1' in msg1

    def test_broadcast_no_clients_no_error(self):
        """Broadcasting with no clients should succeed silently."""
        mgr = SSEManager()
        mgr.broadcast("task_completed", {"task_id": 2})

    def test_disconnected_client_cleanup(self):
        """Full queue (simulating a disconnected client) gets cleaned up."""
        mgr = SSEManager()
        # Create a queue with maxsize=1 so it fills up
        small_q: queue.Queue[str] = queue.Queue(maxsize=1)
        small_q.put("filler")  # Fill it
        with mgr._lock:
            mgr._clients.append(small_q)
        assert mgr.client_count == 1

        # Broadcast should remove the full queue
        mgr.broadcast("test", {"x": 1})
        assert mgr.client_count == 0

    def test_broadcast_sse_format(self):
        mgr = SSEManager()
        q = mgr.add_client()
        mgr.broadcast("qa_passed", {"gate": "pytest"})
        msg = q.get_nowait()
        # SSE format: event line, data line, blank line
        lines = msg.split("\n")
        assert lines[0] == "event: qa_passed"
        assert lines[1].startswith("data: ")
        payload = json.loads(lines[1][len("data: ") :])
        assert payload["gate"] == "pytest"
        assert lines[2] == ""
        assert lines[3] == ""


# ---------------------------------------------------------------------------
# DashboardRequestHandler tests
# ---------------------------------------------------------------------------


class TestDashboardRequestHandler:
    """Test HTTP routes using a real server on a random port."""

    def setup_method(self):
        self.plan = _simple_plan()
        self.hooks = HookRegistry()
        self.dashboard = WebDashboard(
            plan=self.plan,
            hooks=self.hooks,
            host="127.0.0.1",
            port=8420,
        )
        self.url = self.dashboard.start()

    def teardown_method(self):
        self.dashboard.stop()

    def _get(self, path: str) -> tuple[int, str, bytes]:
        """Fetch a path and return (status, content_type, body)."""
        try:
            resp = urllib.request.urlopen(f"{self.url}{path}", timeout=5)
            return resp.status, resp.headers.get("Content-Type", ""), resp.read()
        except urllib.error.HTTPError as e:
            return e.code, e.headers.get("Content-Type", ""), e.read()

    def test_index_returns_html(self):
        status, ct, body = self._get("/")
        assert status == 200
        assert "text/html" in ct
        assert b"Lindy Orchestrator" in body

    def test_api_state_returns_json(self):
        status, ct, body = self._get("/api/state")
        assert status == 200
        assert "application/json" in ct
        data = json.loads(body)
        assert data["goal"] == "Test web dashboard"
        assert len(data["tasks"]) == 2

    def test_api_metrics_returns_json(self):
        status, ct, body = self._get("/api/metrics")
        assert status == 200
        data = json.loads(body)
        assert "total_cost_usd" in data
        assert data["total_cost_usd"] == 0.50

    def test_404_for_unknown_route(self):
        status, _, _ = self._get("/nonexistent")
        assert status == 404

    def test_plan_state_serialization(self):
        status, _, body = self._get("/api/state")
        data = json.loads(body)
        task1 = data["tasks"][0]
        assert task1["id"] == 1
        assert task1["module"] == "mod"
        assert task1["status"] == "completed"
        assert task1["cost_usd"] == 0.50


# ---------------------------------------------------------------------------
# WebDashboard integration tests
# ---------------------------------------------------------------------------


class TestWebDashboard:
    def test_start_and_stop(self):
        plan = _simple_plan()
        hooks = HookRegistry()
        dashboard = WebDashboard(plan=plan, hooks=hooks)
        url = dashboard.start()
        assert url.startswith("http://127.0.0.1:")

        # Verify server is running
        resp = urllib.request.urlopen(f"{url}/api/state", timeout=5)
        assert resp.status == 200

        dashboard.stop()

        # After stop, the server should not respond
        # (give it a moment to shut down)
        time.sleep(0.2)

    def test_hook_event_broadcasts_to_sse(self):
        plan = _simple_plan()
        hooks = HookRegistry()
        dashboard = WebDashboard(plan=plan, hooks=hooks)
        dashboard.start()

        try:
            # Add a direct SSE client queue
            q = dashboard.sse_manager.add_client()

            # Emit an event through hooks
            hooks.emit(
                Event(
                    type=EventType.TASK_STARTED,
                    task_id=1,
                    module="mod",
                    data={"task_id": 1, "description": "do thing"},
                )
            )

            # Should receive the broadcast
            msg = q.get(timeout=5)
            assert "event: task_started" in msg
            payload = json.loads(msg.split("data: ")[1].split("\n")[0])
            assert payload["task_id"] == 1
            assert "plan_state" in payload

            dashboard.sse_manager.remove_client(q)
        finally:
            dashboard.stop()

    def test_port_fallback(self):
        """If default port is taken, dashboard tries next ports."""
        plan = _simple_plan()
        hooks = HookRegistry()

        # Start first dashboard on default port
        d1 = WebDashboard(plan=plan, hooks=hooks, port=8420)
        url1 = d1.start()

        # Second dashboard should bind to a different port
        d2 = WebDashboard(plan=plan, hooks=hooks, port=8420)
        url2 = d2.start()

        assert url1 != url2
        assert "8420" in url1 or "8421" in url1

        d2.stop()
        d1.stop()

    def test_stop_unsubscribes_hooks(self):
        plan = _simple_plan()
        hooks = HookRegistry()
        dashboard = WebDashboard(plan=plan, hooks=hooks)
        dashboard.start()

        initial_count = hooks.handler_count
        assert initial_count > 0

        dashboard.stop()
        assert hooks.handler_count < initial_count
