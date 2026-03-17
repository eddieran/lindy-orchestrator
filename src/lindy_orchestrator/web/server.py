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
  :root {
    --bg: #0d1117; --bg-card: #161b22; --bg-card-active: #1c2333;
    --border: #30363d; --text: #c9d1d9; --text-dim: #8b949e; --text-bright: #f0f6fc;
    --blue: #58a6ff; --green: #3fb950; --red: #f85149; --yellow: #d29922; --purple: #bc8cff;
    --blue-bg: rgba(56,139,253,0.12); --green-bg: rgba(63,185,80,0.12);
    --red-bg: rgba(248,81,73,0.12); --yellow-bg: rgba(210,153,34,0.12);
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: 'SF Mono', 'Fira Code', 'JetBrains Mono', monospace; background: var(--bg); color: var(--text); overflow: hidden; height: 100vh; display: flex; flex-direction: column; }

  /* Header */
  .header { padding: 12px 20px; border-bottom: 1px solid var(--border); display: flex; align-items: center; gap: 16px; flex-shrink: 0; }
  .header h1 { font-size: 14px; color: var(--blue); font-weight: 600; white-space: nowrap; }
  .header .goal { color: var(--text-dim); font-size: 12px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; flex: 1; }
  .header .stats { font-size: 12px; display: flex; gap: 12px; white-space: nowrap; }
  .header .stats span { font-weight: 600; }
  .s-ok { color: var(--green); } .s-fail { color: var(--red); } .s-run { color: var(--blue); } .s-wait { color: var(--text-dim); }

  /* Main layout */
  .main { display: flex; flex: 1; overflow: hidden; }
  .dag-panel { flex: 1; overflow: auto; padding: 24px; position: relative; }
  .sidebar { width: 360px; border-left: 1px solid var(--border); display: flex; flex-direction: column; overflow: hidden; flex-shrink: 0; }

  /* DAG */
  .dag-container { position: relative; min-height: 100%; }
  svg.edges { position: absolute; top: 0; left: 0; width: 100%; height: 100%; pointer-events: none; z-index: 0; }
  svg.edges path { fill: none; stroke: var(--border); stroke-width: 1.5; }
  svg.edges path.active { stroke: var(--blue); stroke-width: 2; }
  svg.edges path.done { stroke: var(--green); stroke-width: 1.5; opacity: 0.6; }
  svg.edges marker path { fill: var(--border); stroke: none; }

  .dag-layer { display: flex; gap: 16px; justify-content: center; margin-bottom: 16px; position: relative; z-index: 1; }

  /* Node card */
  .node { width: 260px; border: 1px solid var(--border); border-radius: 8px; background: var(--bg-card); padding: 10px 12px; cursor: pointer; transition: all 0.2s; position: relative; }
  .node:hover { border-color: var(--text-dim); }
  .node.pending { opacity: 0.5; }
  .node.in_progress { border-color: var(--blue); background: var(--bg-card-active); box-shadow: 0 0 16px rgba(56,139,253,0.15); }
  .node.completed { border-color: var(--green); }
  .node.failed { border-color: var(--red); }
  .node.skipped { opacity: 0.35; }
  .node.selected { outline: 2px solid var(--purple); outline-offset: 2px; }

  .node-header { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }
  .node-icon { width: 18px; height: 18px; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: 10px; flex-shrink: 0; }
  .node-icon.pending { background: var(--border); color: var(--text-dim); }
  .node-icon.in_progress { background: var(--blue-bg); color: var(--blue); animation: pulse 2s infinite; }
  .node-icon.completed { background: var(--green-bg); color: var(--green); }
  .node-icon.failed { background: var(--red-bg); color: var(--red); }
  .node-icon.skipped { background: var(--border); color: var(--text-dim); }
  @keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.5; } }

  .node-id { font-size: 11px; font-weight: 700; color: var(--text-dim); }
  .node-module { font-size: 10px; color: var(--purple); background: rgba(188,140,255,0.1); padding: 1px 6px; border-radius: 4px; }
  .node-desc { font-size: 11px; color: var(--text); line-height: 1.4; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }

  /* Live streaming on node */
  .node-stream { font-size: 10px; color: var(--blue); margin-top: 6px; padding-top: 6px; border-top: 1px solid var(--border); max-height: 40px; overflow: hidden; line-height: 1.3; }
  .node-stream .tool { color: var(--yellow); font-weight: 600; }

  /* Sidebar */
  .sidebar-header { padding: 12px 16px; border-bottom: 1px solid var(--border); font-size: 12px; font-weight: 600; color: var(--blue); }
  .sidebar-content { flex: 1; overflow-y: auto; }

  /* Task detail */
  .task-detail { padding: 16px; display: none; }
  .task-detail.visible { display: block; }
  .td-title { font-size: 13px; font-weight: 600; color: var(--text-bright); margin-bottom: 8px; }
  .td-meta { font-size: 11px; color: var(--text-dim); margin-bottom: 12px; }
  .td-meta span { display: inline-block; margin-right: 12px; }

  /* Streaming log */
  .stream-log { font-size: 11px; line-height: 1.5; max-height: 300px; overflow-y: auto; }
  .stream-log .entry { padding: 3px 0; border-bottom: 1px solid #1c2128; }
  .stream-log .entry .ts { color: var(--text-dim); margin-right: 8px; }
  .stream-log .entry .tool-name { color: var(--yellow); font-weight: 600; }
  .stream-log .entry .reasoning { color: var(--text-dim); font-style: italic; }
  .stream-log .entry .qa-pass { color: var(--green); }
  .stream-log .entry .qa-fail { color: var(--red); }

  /* Event log */
  .event-log { border-top: 1px solid var(--border); max-height: 200px; overflow-y: auto; padding: 8px 16px; font-size: 10px; color: var(--text-dim); flex-shrink: 0; }
  .event-log .ev { padding: 2px 0; }
</style>
</head>
<body>
<div class="header">
  <h1>LINDY ORCHESTRATOR</h1>
  <div class="goal" id="goal"></div>
  <div class="stats" id="stats"></div>
</div>
<div class="main">
  <div class="dag-panel" id="dagPanel">
    <div class="dag-container" id="dagContainer"></div>
  </div>
  <div class="sidebar">
    <div class="sidebar-header">TASK DETAIL</div>
    <div class="sidebar-content">
      <div class="task-detail visible" id="taskDetail">
        <div class="td-title" id="tdTitle">Select a task node</div>
        <div class="td-meta" id="tdMeta"></div>
        <div class="stream-log" id="streamLog"></div>
      </div>
    </div>
    <div class="sidebar-header">EVENT LOG</div>
    <div class="event-log" id="eventLog"></div>
  </div>
</div>

<script>
let tasks = {};
let taskLogs = {};  // task_id -> [{ts, type, text}]
let selectedTask = null;

const icons = { pending: '\\u25CB', in_progress: '\\u25CF', completed: '\\u2713', failed: '\\u2717', skipped: '\\u2015' };
const statusMap = { task_started: 'in_progress', task_completed: 'completed', task_failed: 'failed', task_skipped: 'skipped', task_retrying: 'in_progress' };

/* ---- DAG Layout ---- */
function computeLayers(taskMap) {
  const layers = {};
  const depth = {};
  function getDepth(id) {
    if (depth[id] !== undefined) return depth[id];
    const t = taskMap[id];
    if (!t || !t.depends_on || t.depends_on.length === 0) { depth[id] = 0; return 0; }
    depth[id] = 1 + Math.max(...t.depends_on.map(d => getDepth(d)));
    return depth[id];
  }
  Object.keys(taskMap).forEach(id => getDepth(Number(id)));
  Object.entries(depth).forEach(([id, d]) => {
    if (!layers[d]) layers[d] = [];
    layers[d].push(Number(id));
  });
  return layers;
}

function renderDAG() {
  const container = document.getElementById('dagContainer');
  const layers = computeLayers(tasks);
  const maxLayer = Math.max(...Object.keys(layers).map(Number), 0);
  let html = '';
  for (let l = 0; l <= maxLayer; l++) {
    const ids = (layers[l] || []).sort((a,b) => a - b);
    html += '<div class="dag-layer" data-layer="' + l + '">';
    ids.forEach(id => {
      const t = tasks[id];
      const streamHtml = t._lastTool
        ? '<div class="node-stream"><span class="tool">' + esc(t._lastTool) + '</span> ' + esc(t._lastReasoning || '').substring(0,60) + '</div>'
        : '';
      html += '<div class="node ' + t.status + (selectedTask === id ? ' selected' : '') + '" id="node-' + id + '" onclick="selectTask(' + id + ')">'
        + '<div class="node-header">'
        + '<div class="node-icon ' + t.status + '">' + icons[t.status] + '</div>'
        + '<span class="node-id">T' + id + '</span>'
        + '<span class="node-module">' + esc(t.module) + '</span>'
        + '</div>'
        + '<div class="node-desc">' + esc(t.description) + '</div>'
        + streamHtml
        + '</div>';
    });
    html += '</div>';
  }
  container.innerHTML = '<svg class="edges" id="edgeSvg"></svg>' + html;
  requestAnimationFrame(drawEdges);
}

function drawEdges() {
  const svg = document.getElementById('edgeSvg');
  if (!svg) return;
  const panel = document.getElementById('dagPanel');
  const panelRect = panel.getBoundingClientRect();
  const scrollX = panel.scrollLeft;
  const scrollY = panel.scrollTop;
  let paths = '';
  Object.values(tasks).forEach(t => {
    (t.depends_on || []).forEach(depId => {
      const from = document.getElementById('node-' + depId);
      const to = document.getElementById('node-' + t.id);
      if (!from || !to) return;
      const fr = from.getBoundingClientRect();
      const tr = to.getBoundingClientRect();
      const x1 = fr.left + fr.width/2 - panelRect.left + scrollX;
      const y1 = fr.top + fr.height - panelRect.top + scrollY;
      const x2 = tr.left + tr.width/2 - panelRect.left + scrollX;
      const y2 = tr.top - panelRect.top + scrollY;
      const my = (y1 + y2) / 2;
      const cls = t.status === 'in_progress' ? 'active' : (t.status === 'completed' ? 'done' : '');
      paths += '<path class="' + cls + '" d="M' + x1 + ',' + y1 + ' C' + x1 + ',' + my + ' ' + x2 + ',' + my + ' ' + x2 + ',' + y2 + '"/>';
    });
  });
  svg.innerHTML = '<defs><marker id="arrow" markerWidth="6" markerHeight="4" refX="6" refY="2" orient="auto"><path d="M0,0 L6,2 L0,4" fill="var(--border)"/></marker></defs>' + paths;
  // Resize SVG to container
  const cont = document.getElementById('dagContainer');
  svg.setAttribute('width', cont.scrollWidth);
  svg.setAttribute('height', cont.scrollHeight);
}

/* ---- Stats bar ---- */
function updateStats() {
  const all = Object.values(tasks);
  const ok = all.filter(t => t.status === 'completed').length;
  const fail = all.filter(t => t.status === 'failed').length;
  const run = all.filter(t => t.status === 'in_progress').length;
  const wait = all.filter(t => t.status === 'pending').length;
  const skip = all.filter(t => t.status === 'skipped').length;
  document.getElementById('stats').innerHTML =
    '<span class="s-ok">' + ok + ' \\u2713</span>' +
    '<span class="s-fail">' + fail + ' \\u2717</span>' +
    '<span class="s-run">' + run + ' \\u25CF</span>' +
    '<span class="s-wait">' + (wait + skip) + ' \\u25CB</span>' +
    '<span style="color:var(--text-dim)">' + all.length + ' total</span>';
}

/* ---- Sidebar detail ---- */
function selectTask(id) {
  selectedTask = id;
  const t = tasks[id];
  document.getElementById('tdTitle').textContent = 'T' + id + ': ' + t.description;
  const deps = (t.depends_on || []).map(d => 'T' + d).join(', ') || 'none';
  document.getElementById('tdMeta').innerHTML =
    '<span>Module: <b>' + esc(t.module) + '</b></span>' +
    '<span>Status: <b>' + t.status + '</b></span>' +
    '<span>Deps: ' + deps + '</span>';
  renderStreamLog(id);
  renderDAG();
}

function renderStreamLog(id) {
  const log = taskLogs[id] || [];
  const el = document.getElementById('streamLog');
  el.innerHTML = log.map(e => {
    let content = '';
    if (e.tool) content += '<span class="tool-name">' + esc(e.tool) + '</span> ';
    if (e.reasoning) content += '<span class="reasoning">' + esc(e.reasoning) + '</span>';
    if (e.qa) content += '<span class="' + (e.qa.passed ? 'qa-pass' : 'qa-fail') + '">' + (e.qa.passed ? 'PASS' : 'FAIL') + ' ' + esc(e.qa.gate) + '</span>';
    if (e.text) content += esc(e.text);
    return '<div class="entry"><span class="ts">' + e.ts + '</span>' + content + '</div>';
  }).join('');
  el.scrollTop = el.scrollHeight;
}

/* ---- Event log ---- */
function addEventLog(ev) {
  const el = document.getElementById('eventLog');
  const ts = new Date().toLocaleTimeString();
  const div = document.createElement('div');
  div.className = 'ev';
  div.textContent = '[' + ts + '] ' + ev.type + (ev.task_id ? ' (T' + ev.task_id + ')' : '');
  el.prepend(div);
  while (el.children.length > 100) el.removeChild(el.lastChild);
}

/* ---- Utilities ---- */
function esc(s) { if (!s) return ''; const d = document.createElement('div'); d.textContent = s; return d.innerHTML; }
function ts() { return new Date().toLocaleTimeString(); }

function addTaskLog(taskId, entry) {
  if (!taskLogs[taskId]) taskLogs[taskId] = [];
  taskLogs[taskId].push(entry);
  if (taskLogs[taskId].length > 200) taskLogs[taskId].shift();
  if (selectedTask === taskId) renderStreamLog(taskId);
}

/* ---- SSE ---- */
const src = new EventSource('/events');
src.addEventListener('init', e => {
  const d = JSON.parse(e.data);
  document.getElementById('goal').textContent = d.goal.substring(0, 120);
  d.tasks.forEach(t => { t._lastTool = ''; t._lastReasoning = ''; tasks[t.id] = t; });
  renderDAG();
  updateStats();
});

src.addEventListener('hook', e => {
  const ev = JSON.parse(e.data);
  const tid = ev.task_id;
  if (tid && tasks[tid]) {
    if (statusMap[ev.type]) {
      tasks[tid].status = statusMap[ev.type];
      addTaskLog(tid, { ts: ts(), text: ev.type.replace('task_', '').toUpperCase() });
    }
    if (ev.type === 'task_heartbeat' && ev.data) {
      const tool = ev.data.tool || '';
      const reasoning = ev.data.reasoning || '';
      if (tool) {
        tasks[tid]._lastTool = tool;
        addTaskLog(tid, { ts: ts(), tool: tool });
      }
      if (reasoning) {
        tasks[tid]._lastReasoning = reasoning;
        addTaskLog(tid, { ts: ts(), reasoning: reasoning.substring(0, 120) });
      }
    }
    if (ev.type === 'qa_passed' || ev.type === 'qa_failed') {
      addTaskLog(tid, { ts: ts(), qa: { passed: ev.type === 'qa_passed', gate: ev.data?.gate || '?' } });
    }
  }
  addEventLog(ev);
  renderDAG();
  updateStats();
});

src.onerror = () => {};

// Redraw edges on scroll/resize
window.addEventListener('resize', () => requestAnimationFrame(drawEdges));
document.getElementById('dagPanel').addEventListener('scroll', () => requestAnimationFrame(drawEdges));
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
                    "depends_on": t.depends_on,
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
