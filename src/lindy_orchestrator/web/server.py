"""Lightweight web dashboard for real-time plan monitoring.

Serves a single-page HTML dashboard over HTTP and streams hook events
via Server-Sent Events (SSE).  Uses only the stdlib ``http.server`` so
no extra dependencies are required.
"""

from __future__ import annotations

import json
import logging
import queue
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

from ..hooks import Event, HookRegistry
from ..models import TaskPlan

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# HTML page (embedded to stay zero-dependency)
# ---------------------------------------------------------------------------

_INDEX_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Lindy Orchestrator</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: system-ui, -apple-system, sans-serif; background: #0d1117; color: #c9d1d9; padding: 1.5rem; }
  h1 { font-size: 1.25rem; margin-bottom: 1rem; color: #58a6ff; }
  .goal { color: #8b949e; margin-bottom: 1rem; font-size: 0.9rem; }
  .task { padding: 0.5rem 0.75rem; margin: 0.25rem 0; border-radius: 6px; background: #161b22; border-left: 3px solid #30363d; font-size: 0.85rem; }
  .task.pending   { border-left-color: #484f58; }
  .task.in_progress { border-left-color: #1f6feb; background: #161b22; }
  .task.completed { border-left-color: #3fb950; }
  .task.failed    { border-left-color: #f85149; }
  .task.skipped   { border-left-color: #484f58; opacity: 0.6; }
  .status { display: inline-block; min-width: 5rem; font-weight: 600; }
  .status.pending   { color: #484f58; }
  .status.in_progress { color: #58a6ff; }
  .status.completed { color: #3fb950; }
  .status.failed    { color: #f85149; }
  .status.skipped   { color: #484f58; }
  #events { margin-top: 1rem; max-height: 14rem; overflow-y: auto; font-size: 0.8rem; color: #8b949e; background: #0d1117; border: 1px solid #21262d; border-radius: 6px; padding: 0.5rem; }
  .event-line { padding: 0.15rem 0; border-bottom: 1px solid #161b22; }
</style>
</head>
<body>
<h1>Lindy Orchestrator</h1>
<div class="goal" id="goal"></div>
<div id="tasks"></div>
<h3 style="margin-top:1rem;font-size:0.9rem;color:#58a6ff;">Event Log</h3>
<div id="events"></div>
<script>
const tasksEl = document.getElementById('tasks');
const eventsEl = document.getElementById('events');
const goalEl = document.getElementById('goal');
let tasks = {};

function render() {
  const ids = Object.keys(tasks).sort((a,b) => Number(a) - Number(b));
  tasksEl.innerHTML = ids.map(id => {
    const t = tasks[id];
    return `<div class="task ${t.status}"><span class="status ${t.status}">${t.status}</span> <strong>${id}.</strong> [${t.module}] ${t.description}</div>`;
  }).join('');
}

const src = new EventSource('/events');
src.addEventListener('init', e => {
  const d = JSON.parse(e.data);
  goalEl.textContent = 'Goal: ' + d.goal;
  d.tasks.forEach(t => { tasks[t.id] = t; });
  render();
});
src.addEventListener('hook', e => {
  const ev = JSON.parse(e.data);
  if (ev.task_id && tasks[ev.task_id]) {
    const statusMap = {
      task_started: 'in_progress', task_completed: 'completed',
      task_failed: 'failed', task_skipped: 'skipped', task_retrying: 'in_progress',
    };
    if (statusMap[ev.type]) tasks[ev.task_id].status = statusMap[ev.type];
    render();
  }
  const line = document.createElement('div');
  line.className = 'event-line';
  line.textContent = `[${new Date().toLocaleTimeString()}] ${ev.type}` + (ev.task_id ? ` (task ${ev.task_id})` : '');
  eventsEl.prepend(line);
});
src.onerror = () => { /* reconnect is automatic */ };
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Request handler
# ---------------------------------------------------------------------------


class _Handler(BaseHTTPRequestHandler):
    """HTTP handler for the web dashboard.

    Attributes on the *server* instance (set by ``WebDashboard``):
      - ``plan``: the current ``TaskPlan``
      - ``event_queues``: list of ``queue.Queue`` for SSE clients
      - ``queues_lock``: threading lock for ``event_queues``
    """

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        """Suppress default stderr logging."""

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/":
            self._serve_html()
        elif self.path == "/events":
            self._serve_sse()
        elif self.path == "/health":
            self._respond(200, "application/json", json.dumps({"ok": True}))
        else:
            self._respond(404, "text/plain", "Not Found")

    # -- helpers --------------------------------------------------------------

    def _respond(self, code: int, content_type: str, body: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body.encode())

    def _serve_html(self) -> None:
        self._respond(200, "text/html; charset=utf-8", _INDEX_HTML)

    def _serve_sse(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        # Send initial plan snapshot
        plan: TaskPlan = self.server.plan  # type: ignore[attr-defined]
        init_data = {
            "goal": plan.goal,
            "tasks": [
                {
                    "id": t.id,
                    "module": t.module,
                    "description": t.description,
                    "status": t.status.value,
                }
                for t in plan.tasks
            ],
        }
        self._write_sse("init", init_data)

        # Stream events
        q: queue.Queue[dict | None] = queue.Queue()
        with self.server.queues_lock:  # type: ignore[attr-defined]
            self.server.event_queues.append(q)  # type: ignore[attr-defined]
        try:
            while True:
                msg = q.get()
                if msg is None:
                    break
                self._write_sse("hook", msg)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            with self.server.queues_lock:  # type: ignore[attr-defined]
                if q in self.server.event_queues:  # type: ignore[attr-defined]
                    self.server.event_queues.remove(q)  # type: ignore[attr-defined]

    def _write_sse(self, event: str, data: dict) -> None:
        payload = f"event: {event}\ndata: {json.dumps(data)}\n\n"
        self.wfile.write(payload.encode())
        self.wfile.flush()


# ---------------------------------------------------------------------------
# WebDashboard public API
# ---------------------------------------------------------------------------


class WebDashboard:
    """Browser-based dashboard that streams plan events over SSE.

    Parameters
    ----------
    plan : TaskPlan
        The execution plan to monitor.
    hooks : HookRegistry
        Hook registry to subscribe to for events.
    metrics_collector : object | None
        Optional metrics collector (reserved for future use).
    port : int
        HTTP port to listen on (default 8420).
    """

    def __init__(
        self,
        plan: TaskPlan,
        hooks: HookRegistry,
        metrics_collector: Any | None = None,
        port: int = 8420,
    ) -> None:
        self._plan = plan
        self._hooks = hooks
        self._metrics_collector = metrics_collector
        self._port = port
        self._server: HTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def port(self) -> int:
        return self._port

    @property
    def url(self) -> str:
        return f"http://localhost:{self._port}"

    def start(self) -> None:
        """Start the HTTP server in a daemon thread and subscribe to hooks."""
        server = HTTPServer(("127.0.0.1", self._port), _Handler)
        # Attach shared state to server so handlers can access it
        server.plan = self._plan  # type: ignore[attr-defined]
        server.event_queues = []  # type: ignore[attr-defined]
        server.queues_lock = threading.Lock()  # type: ignore[attr-defined]
        self._server = server

        self._hooks.on_any(self._on_event)

        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self._thread = thread

    def stop(self) -> None:
        """Shut down the server and unsubscribe from hooks."""
        self._hooks.remove_any(self._on_event)
        if self._server is not None:
            # Signal all SSE clients to disconnect
            with self._server.queues_lock:  # type: ignore[attr-defined]
                for q in self._server.event_queues:  # type: ignore[attr-defined]
                    q.put(None)
            self._server.shutdown()
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def _on_event(self, event: Event) -> None:
        """Forward hook events to all connected SSE clients."""
        if self._server is None:
            return
        data = {
            "type": event.type.value,
            "task_id": event.task_id,
            "module": event.module,
            "data": event.data,
            "timestamp": event.timestamp,
        }
        with self._server.queues_lock:  # type: ignore[attr-defined]
            for q in self._server.event_queues:  # type: ignore[attr-defined]
                q.put(data)
