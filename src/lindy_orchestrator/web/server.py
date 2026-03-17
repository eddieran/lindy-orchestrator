"""Stdlib-only web dashboard with SSE for live task DAG visualization.

Provides a browser-based dashboard showing task status, cost accumulation,
and QA results in real time via Server-Sent Events.  Zero external
dependencies — uses only ``http.server``, ``threading``, and ``json``.
"""

from __future__ import annotations

import json
import logging
import queue
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

from lindy_orchestrator.hooks import Event, HookRegistry
from lindy_orchestrator.models import TaskPlan, plan_to_dict

log = logging.getLogger(__name__)

_STATIC_DIR = Path(__file__).parent / "static"


# ---------------------------------------------------------------------------
# SSE manager
# ---------------------------------------------------------------------------


class SSEManager:
    """Manages Server-Sent Event client connections.

    Each connected client gets a :class:`queue.Queue`.  ``broadcast()``
    pushes events to all queues; disconnected clients are silently removed.
    """

    def __init__(self) -> None:
        self._clients: list[queue.Queue[str | None]] = []
        self._lock = threading.Lock()

    def add_client(self) -> queue.Queue[str | None]:
        q: queue.Queue[str | None] = queue.Queue()
        with self._lock:
            self._clients.append(q)
        return q

    def remove_client(self, q: queue.Queue[str | None]) -> None:
        with self._lock:
            try:
                self._clients.remove(q)
            except ValueError:
                pass

    def broadcast(self, event_type: str, data: Any) -> None:
        """Push an SSE-formatted message to every connected client."""
        payload = json.dumps(data, default=str)
        message = f"event: {event_type}\ndata: {payload}\n\n"
        with self._lock:
            for q in list(self._clients):
                try:
                    q.put_nowait(message)
                except queue.Full:
                    pass  # slow client — drop message

    @property
    def client_count(self) -> int:
        with self._lock:
            return len(self._clients)


# ---------------------------------------------------------------------------
# Request handler
# ---------------------------------------------------------------------------


class DashboardRequestHandler(BaseHTTPRequestHandler):
    """Routes requests for the web dashboard.

    Routes:
        GET /             → static/index.html
        GET /static/*     → static files
        GET /api/events   → SSE stream
        GET /api/state    → current plan state JSON
        GET /api/metrics  → metrics snapshot JSON
    """

    # Set by WebDashboard via functools.partial or server attribute
    sse_manager: SSEManager
    plan: TaskPlan
    metrics_fn: Any  # callable returning dict, or None

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?")[0]

        if path == "/" or path == "/index.html":
            self._serve_index()
        elif path.startswith("/static/"):
            self._serve_static(path[len("/static/") :])
        elif path == "/api/events":
            self._serve_sse()
        elif path == "/api/state":
            self._serve_state()
        elif path == "/api/metrics":
            self._serve_metrics()
        else:
            self.send_error(404)

    # -- route handlers ---------------------------------------------------

    def _serve_index(self) -> None:
        index = _STATIC_DIR / "index.html"
        if not index.exists():
            self.send_error(404, "index.html not found")
            return
        body = index.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_static(self, rel_path: str) -> None:
        # Prevent directory traversal
        safe = Path(rel_path).name
        fpath = _STATIC_DIR / safe
        if not fpath.exists() or not fpath.is_file():
            self.send_error(404)
            return
        body = fpath.read_bytes()
        ct = _guess_content_type(fpath.suffix)
        self.send_response(200)
        self.send_header("Content-Type", ct)
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

        client_q = self.server.sse_manager.add_client()  # type: ignore[attr-defined]
        try:
            while True:
                try:
                    message = client_q.get(timeout=30)
                except queue.Empty:
                    # Send keep-alive comment
                    try:
                        self.wfile.write(b": keepalive\n\n")
                        self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError):
                        break
                    continue

                if message is None:
                    break  # shutdown signal

                try:
                    self.wfile.write(message.encode("utf-8"))
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    break
        finally:
            self.server.sse_manager.remove_client(client_q)  # type: ignore[attr-defined]

    def _serve_state(self) -> None:
        plan = self.server.plan  # type: ignore[attr-defined]
        body = json.dumps(plan_to_dict(plan), default=str).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_metrics(self) -> None:
        metrics_fn = self.server.metrics_fn  # type: ignore[attr-defined]
        if metrics_fn is not None:
            try:
                data = metrics_fn()
            except Exception:
                data = {}
        else:
            # Build basic metrics from plan
            plan = self.server.plan  # type: ignore[attr-defined]
            data = _basic_metrics(plan)

        body = json.dumps(data, default=str).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # -- logging ----------------------------------------------------------

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        """Silence default request logging."""
        pass


# ---------------------------------------------------------------------------
# WebDashboard
# ---------------------------------------------------------------------------


class WebDashboard:
    """Browser-based live dashboard for task execution.

    Uses SSE to push hook events to connected browsers in real time.

    Args:
        plan: The task plan being executed.
        hooks: Hook registry to subscribe to.
        metrics: Optional callable returning a metrics dict snapshot.
        host: Bind address (default ``127.0.0.1``).
        port: Preferred port (default ``8420``); tries up to ``8430`` on conflict.
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
        self._metrics_fn = metrics
        self._host = host
        self._port = port
        self._sse = SSEManager()
        self._server: HTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._handler = self._on_event

    def start(self) -> str:
        """Start the dashboard server and return the URL.

        Tries ports ``port`` through ``port + 10``.  Raises ``RuntimeError``
        if no port is available.
        """
        server = self._try_bind()
        # Attach shared state to the server instance so the handler can access it
        server.sse_manager = self._sse  # type: ignore[attr-defined]
        server.plan = self._plan  # type: ignore[attr-defined]
        server.metrics_fn = self._metrics_fn  # type: ignore[attr-defined]
        self._server = server

        self._thread = threading.Thread(target=server.serve_forever, daemon=True)
        self._thread.start()

        # Subscribe to all hook events
        self._hooks.on_any(self._handler)

        url = f"http://{self._host}:{self._port}"
        log.info("Web dashboard started at %s", url)
        return url

    def stop(self) -> None:
        """Stop the server and unsubscribe from hooks."""
        self._hooks.remove_any(self._handler)

        # Signal all SSE clients to disconnect
        self._sse.broadcast("shutdown", {"reason": "server stopping"})

        if self._server is not None:
            self._server.shutdown()
            self._server = None

        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def _on_event(self, event: Event) -> None:
        """Convert a hook event to an SSE broadcast."""
        data = {
            "type": event.type.value,
            "timestamp": event.timestamp,
            "task_id": event.task_id,
            "module": event.module,
            "data": event.data,
        }
        self._sse.broadcast("task_event", data)

    def _try_bind(self) -> HTTPServer:
        """Try binding to ports ``self._port`` through ``self._port + 10``."""
        for offset in range(11):
            port = self._port + offset
            try:
                server = HTTPServer((self._host, port), DashboardRequestHandler)
                self._port = port
                return server
            except OSError:
                continue
        raise RuntimeError(
            f"Could not bind web dashboard to any port in {self._port}-{self._port + 10}"
        )

    @property
    def port(self) -> int:
        return self._port

    @property
    def url(self) -> str:
        return f"http://{self._host}:{self._port}"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _basic_metrics(plan: TaskPlan) -> dict[str, Any]:
    """Build basic metrics from plan state when no MetricsCollector is available."""
    counts: dict[str, int] = {
        "pending": 0,
        "in_progress": 0,
        "completed": 0,
        "failed": 0,
        "skipped": 0,
    }
    total_cost = 0.0
    for t in plan.tasks:
        counts[t.status.value] = counts.get(t.status.value, 0) + 1
        total_cost += t.cost_usd
    return {
        "task_counts": counts,
        "total_tasks": len(plan.tasks),
        "total_cost_usd": round(total_cost, 4),
    }


def _guess_content_type(suffix: str) -> str:
    """Map file extension to Content-Type."""
    return {
        ".html": "text/html; charset=utf-8",
        ".css": "text/css; charset=utf-8",
        ".js": "application/javascript; charset=utf-8",
        ".json": "application/json",
        ".png": "image/png",
        ".svg": "image/svg+xml",
    }.get(suffix.lower(), "application/octet-stream")
