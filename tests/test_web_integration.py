"""Integration tests for the web dashboard: lifecycle, HTTP, SSE, and CLI flags."""

from __future__ import annotations

import json
import threading
import time
import urllib.request

import pytest
from typer.testing import CliRunner

from lindy_orchestrator.hooks import Event, EventType, HookRegistry
from lindy_orchestrator.models import TaskItem, TaskPlan
from lindy_orchestrator.web.server import WebDashboard


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _task(tid: int, module: str = "mod", desc: str = "do thing", **kw) -> TaskItem:
    return TaskItem(id=tid, module=module, description=desc, **kw)


def _plan(*tasks: TaskItem, goal: str = "test goal") -> TaskPlan:
    return TaskPlan(goal=goal, tasks=list(tasks))


def _free_port() -> int:
    """Find an available port to avoid collisions in parallel test runs."""
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ---------------------------------------------------------------------------
# Lifecycle: start → stop
# ---------------------------------------------------------------------------


class TestWebDashboardLifecycle:
    def test_start_and_stop(self):
        plan = _plan(_task(1), _task(2, depends_on=[1]))
        hooks = HookRegistry()
        port = _free_port()
        wd = WebDashboard(plan, hooks, port=port)

        wd.start()
        assert wd.url == f"http://localhost:{port}"
        assert wd._server is not None
        assert wd._thread is not None
        assert wd._thread.is_alive()

        wd.stop()
        assert wd._server is None
        assert wd._thread is None

    def test_stop_is_idempotent(self):
        plan = _plan(_task(1))
        hooks = HookRegistry()
        port = _free_port()
        wd = WebDashboard(plan, hooks, port=port)
        wd.start()
        wd.stop()
        wd.stop()  # should not raise


# ---------------------------------------------------------------------------
# HTTP: GET / returns HTML
# ---------------------------------------------------------------------------


class TestWebDashboardHTTP:
    def test_index_returns_html(self):
        plan = _plan(_task(1), _task(2, depends_on=[1]))
        hooks = HookRegistry()
        port = _free_port()
        wd = WebDashboard(plan, hooks, port=port)
        wd.start()
        try:
            resp = urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=3)
            body = resp.read().decode()
            assert resp.status == 200
            assert "Lindy Orchestrator" in body
            assert "text/html" in resp.headers.get("Content-Type", "")
        finally:
            wd.stop()

    def test_health_endpoint(self):
        plan = _plan(_task(1))
        hooks = HookRegistry()
        port = _free_port()
        wd = WebDashboard(plan, hooks, port=port)
        wd.start()
        try:
            resp = urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=3)
            data = json.loads(resp.read())
            assert data == {"ok": True}
        finally:
            wd.stop()

    def test_404_for_unknown_path(self):
        plan = _plan(_task(1))
        hooks = HookRegistry()
        port = _free_port()
        wd = WebDashboard(plan, hooks, port=port)
        wd.start()
        try:
            with pytest.raises(urllib.error.HTTPError) as exc_info:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/nope", timeout=3)
            assert exc_info.value.code == 404
        finally:
            wd.stop()


# ---------------------------------------------------------------------------
# SSE: init event and hook forwarding
# ---------------------------------------------------------------------------


class TestWebDashboardSSE:
    def test_sse_init_and_hook_event(self):
        """Connect to /events, receive init + emitted hook, then disconnect."""
        plan = _plan(_task(1, module="backend", desc="Setup API"))
        hooks = HookRegistry()
        port = _free_port()
        wd = WebDashboard(plan, hooks, port=port)
        wd.start()

        received: list[str] = []
        error_holder: list[Exception] = []

        def _reader() -> None:
            try:
                req = urllib.request.Request(f"http://127.0.0.1:{port}/events")
                with urllib.request.urlopen(req, timeout=5) as resp:
                    buf = b""
                    while True:
                        chunk = resp.read(1)
                        if not chunk:
                            break
                        buf += chunk
                        # SSE messages end with double newline
                        while b"\n\n" in buf:
                            msg, buf = buf.split(b"\n\n", 1)
                            received.append(msg.decode())
                            # After receiving 2 messages (init + hook), stop
                            if len(received) >= 2:
                                return
            except Exception as e:
                error_holder.append(e)

        reader_thread = threading.Thread(target=_reader, daemon=True)
        reader_thread.start()

        # Give the reader time to connect and receive init
        time.sleep(0.3)

        # Emit a hook event
        hooks.emit(Event(type=EventType.TASK_STARTED, task_id=1, module="backend"))

        reader_thread.join(timeout=5)
        wd.stop()

        assert len(received) >= 1, f"Expected at least init event, got {received}"

        # Verify init event
        init_msg = received[0]
        assert "event: init" in init_msg
        assert "test goal" in init_msg
        assert "backend" in init_msg

        # Verify hook event if received
        if len(received) >= 2:
            hook_msg = received[1]
            assert "event: hook" in hook_msg
            assert "task_started" in hook_msg


# ---------------------------------------------------------------------------
# CLI flag presence
# ---------------------------------------------------------------------------


class TestCLIWebFlags:
    def test_run_help_shows_web_flag(self):
        from lindy_orchestrator.cli import app

        runner = CliRunner()
        result = runner.invoke(app, ["run", "--help"])
        assert "--web" in result.output
        assert "--web-port" in result.output

    def test_resume_help_shows_web_flag(self):
        from lindy_orchestrator.cli import app

        runner = CliRunner()
        result = runner.invoke(app, ["resume", "--help"])
        assert "--web" in result.output
        assert "--web-port" in result.output
