"""SSE-driven browser dashboard for lindy-orchestrator.

Pure stdlib implementation using http.server, threading, and json.
No external web framework dependencies.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

from ..hooks import Event, HookRegistry
from ..models import TaskPlan, plan_to_dict

log = logging.getLogger(__name__)

_STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")

_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".png": "image/png",
    ".svg": "image/svg+xml",
}


class SSEManager:
    """Manages Server-Sent Events client queues.

    Thread-safe: clients can be added/removed from any thread,
    and broadcast pushes events to all connected clients.
    """

    def __init__(self) -> None:
        self._clients: list[queue.Queue[str]] = []
        self._lock = threading.Lock()

    def add_client(self) -> queue.Queue[str]:
        """Register a new SSE client and return its message queue."""
        q: queue.Queue[str] = queue.Queue()
        with self._lock:
            self._clients.append(q)
        return q

    def remove_client(self, q: queue.Queue[str]) -> None:
        """Unregister a client queue."""
        with self._lock:
            try:
                self._clients.remove(q)
            except ValueError:
                pass

    def broadcast(self, event_type: str, data: dict[str, Any]) -> None:
        """Send an event to all connected clients.

        Disconnected clients (full queues) are silently removed.
        """
        msg = f"event: {event_type}\ndata: {json.dumps(data)}\n\n"
        stale: list[queue.Queue[str]] = []
        with self._lock:
            for q in self._clients:
                try:
                    q.put_nowait(msg)
                except queue.Full:
                    stale.append(q)
            for q in stale:
                try:
                    self._clients.remove(q)
                except ValueError:
                    pass

    @property
    def client_count(self) -> int:
        with self._lock:
            return len(self._clients)


def _make_handler(
    sse_manager: SSEManager,
    plan: TaskPlan,
    metrics: Any | None,
) -> type[BaseHTTPRequestHandler]:
    """Factory that creates a request handler class with closure over shared state."""

    class DashboardRequestHandler(BaseHTTPRequestHandler):
        """HTTP request handler for the web dashboard."""

        def do_GET(self) -> None:  # noqa: N802
            path = self.path.split("?")[0]

            if path == "/":
                self._serve_static("index.html")
            elif path.startswith("/static/"):
                filename = path[len("/static/") :]
                self._serve_static(filename)
            elif path == "/api/events":
                self._serve_sse()
            elif path == "/api/state":
                self._serve_json(plan_to_dict(plan))
            elif path == "/api/metrics":
                self._serve_metrics()
            else:
                self.send_error(404, "Not Found")

        def _serve_static(self, filename: str) -> None:
            filepath = os.path.join(_STATIC_DIR, filename)
            if not os.path.isfile(filepath):
                self.send_error(404, "Not Found")
                return

            ext = os.path.splitext(filename)[1]
            content_type = _CONTENT_TYPES.get(ext, "application/octet-stream")

            with open(filepath, "rb") as f:
                body = f.read()

            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _serve_sse(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()

            client_queue = sse_manager.add_client()
            try:
                while True:
                    try:
                        msg = client_queue.get(timeout=30)
                    except queue.Empty:
                        # Send keepalive comment
                        msg = ": keepalive\n\n"
                    self.wfile.write(msg.encode("utf-8"))
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            finally:
                sse_manager.remove_client(client_queue)

        def _serve_json(self, data: Any) -> None:
            body = json.dumps(data).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)

        def _serve_metrics(self) -> None:
            if metrics is not None and hasattr(metrics, "snapshot"):
                data = metrics.snapshot()
            else:
                # Build basic metrics from plan state
                costs = [t.cost_usd for t in plan.tasks]
                data = {
                    "total_cost_usd": sum(costs),
                    "task_count": len(plan.tasks),
                }
            self._serve_json(data)

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            """Suppress default request logging."""

    return DashboardRequestHandler


class WebDashboard:
    """SSE-driven browser dashboard.

    Hooks into the HookRegistry to broadcast task events to connected
    browser clients via Server-Sent Events.
    """

    def __init__(
        self,
        plan: TaskPlan,
        hooks: HookRegistry,
        metrics: Any | None = None,
        host: str = "127.0.0.1",
        port: int = 8420,
    ) -> None:
        self._plan = plan
        self._hooks = hooks
        self._metrics = metrics
        self._host = host
        self._port = port
        self._sse = SSEManager()
        self._server: HTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> str:
        """Start the dashboard HTTP server in a daemon thread.

        Tries ports 8420-8430. Returns the URL string on success.
        Raises RuntimeError if all ports fail.
        """
        handler_cls = _make_handler(self._sse, self._plan, self._metrics)

        for port in range(self._port, self._port + 11):
            try:
                server = HTTPServer((self._host, port), handler_cls)
                break
            except OSError:
                continue
        else:
            raise RuntimeError(
                f"Could not bind to any port in range {self._port}-{self._port + 10}"
            )

        self._server = server
        self._port = port

        self._thread = threading.Thread(
            target=server.serve_forever,
            daemon=True,
            name="web-dashboard",
        )
        self._thread.start()

        # Subscribe to all hook events
        self._hooks.on_any(self._on_event)

        url = f"http://{self._host}:{port}"
        log.info("Web dashboard started at %s", url)
        return url

    def stop(self) -> None:
        """Shutdown the server and unsubscribe from hooks."""
        self._hooks.remove_any(self._on_event)
        if self._server is not None:
            self._server.shutdown()
            self._server = None
        self._thread = None

    def _on_event(self, event: Event) -> None:
        """Broadcast a hook event to all SSE clients."""
        data: dict[str, Any] = {
            "type": event.type.value,
            "timestamp": event.timestamp,
            "task_id": event.task_id,
            "module": event.module,
            "data": event.data,
        }
        # Also include current plan state for convenience
        data["plan_state"] = plan_to_dict(self._plan)
        self._sse.broadcast(event.type.value, data)

    @property
    def sse_manager(self) -> SSEManager:
        """Expose SSE manager for testing."""
        return self._sse

    @property
    def url(self) -> str | None:
        if self._server is None:
            return None
        return f"http://{self._host}:{self._port}"
