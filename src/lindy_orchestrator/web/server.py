"""Lightweight web dashboard for real-time pipeline monitoring."""

from __future__ import annotations

import json
import logging
import queue
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from ..command_queue import CommandQueue
from ..hooks import Event, HookRegistry
from ..models import ExecutionResult, TaskPlan, TaskState, coerce_execution_result

log = logging.getLogger(__name__)

_INDEX_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Lindy Orchestrator</title>
<style>
  :root {
    --bg: #0d1117;
    --panel: #161b22;
    --panel-active: #1d2430;
    --border: #30363d;
    --text: #c9d1d9;
    --text-dim: #8b949e;
    --blue: #58a6ff;
    --green: #3fb950;
    --red: #f85149;
    --yellow: #d29922;
    --purple: #bc8cff;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    font-family: "SF Mono", "JetBrains Mono", monospace;
    background: var(--bg);
    color: var(--text);
    min-height: 100vh;
  }
  .header {
    padding: 16px 20px;
    border-bottom: 1px solid var(--border);
    display: flex;
    gap: 16px;
    align-items: center;
  }
  .header h1 { margin: 0; font-size: 14px; color: var(--blue); }
  .goal { flex: 1; font-size: 12px; color: var(--text-dim); }
  .stats { display: flex; gap: 12px; font-size: 12px; }
  .layout {
    display: grid;
    grid-template-columns: minmax(0, 1fr) 380px;
    min-height: calc(100vh - 58px);
  }
  .dag-panel {
    padding: 20px;
    overflow-y: auto;
  }
  .dag-layer {
    display: flex;
    gap: 16px;
    flex-wrap: wrap;
    margin-bottom: 16px;
  }
  .node {
    width: 280px;
    border: 1px solid var(--border);
    border-radius: 10px;
    background: var(--panel);
    padding: 12px;
    cursor: pointer;
  }
  .node.in_progress { border-color: var(--blue); background: var(--panel-active); }
  .node.completed { border-color: var(--green); }
  .node.failed { border-color: var(--red); }
  .node.selected { outline: 2px solid var(--purple); }
  .node-top {
    display: flex;
    justify-content: space-between;
    gap: 8px;
    margin-bottom: 8px;
    font-size: 11px;
  }
  .node-desc { font-size: 12px; line-height: 1.45; margin-bottom: 8px; }
  .node-meta, .node-phase {
    font-size: 11px;
    color: var(--text-dim);
  }
  .sidebar {
    border-left: 1px solid var(--border);
    display: flex;
    flex-direction: column;
    min-height: calc(100vh - 58px);
  }
  .section {
    padding: 16px;
    border-bottom: 1px solid var(--border);
  }
  .section h2 {
    margin: 0 0 12px;
    font-size: 11px;
    color: var(--text-dim);
    text-transform: uppercase;
    letter-spacing: 0.08em;
  }
  .task-title { font-size: 13px; margin-bottom: 8px; }
  .task-meta { font-size: 11px; color: var(--text-dim); margin-bottom: 10px; }
  .phase-dots { display: flex; gap: 10px; margin-bottom: 10px; }
  .phase-dot {
    width: 10px;
    height: 10px;
    border-radius: 50%;
    background: var(--border);
  }
  .phase-dot.active.plan { background: var(--purple); }
  .phase-dot.active.generate { background: var(--blue); }
  .phase-dot.active.evaluate { background: var(--yellow); }
  .phase-dot.active.done { background: var(--green); }
  .criteria {
    white-space: pre-wrap;
    line-height: 1.45;
    font-size: 12px;
    color: var(--text-dim);
  }
  .controls {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 8px;
  }
  button {
    border: 1px solid var(--border);
    background: var(--panel);
    color: var(--text);
    border-radius: 8px;
    padding: 10px;
    font-family: inherit;
    font-size: 11px;
    cursor: pointer;
  }
  button:disabled { opacity: 0.4; cursor: not-allowed; }
  .cost-grid {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 8px;
    font-size: 11px;
  }
  .cost-card, .attempt-row {
    border: 1px solid var(--border);
    background: var(--panel);
    border-radius: 8px;
    padding: 10px;
  }
  .attempts {
    display: grid;
    gap: 8px;
    max-height: 280px;
    overflow-y: auto;
  }
  .attempt-row {
    display: grid;
    grid-template-columns: 34px 54px 1fr;
    gap: 8px;
    font-size: 11px;
    align-items: start;
  }
  .event-log {
    padding: 16px;
    overflow-y: auto;
    font-size: 11px;
    color: var(--text-dim);
    flex: 1;
  }
  .event-log div { margin-bottom: 6px; }
  @media (max-width: 960px) {
    .layout { grid-template-columns: 1fr; }
    .sidebar { border-left: none; border-top: 1px solid var(--border); }
  }
</style>
</head>
<body>
<div class="header">
  <h1>LINDY ORCHESTRATOR</h1>
  <div class="goal" id="goal"></div>
  <div class="stats" id="stats"></div>
</div>
<div class="layout">
  <div class="dag-panel" id="dagPanel"></div>
  <div class="sidebar">
    <div class="section">
      <h2>Selected Task</h2>
      <div class="task-title" id="taskTitle">Select a task</div>
      <div class="task-meta" id="taskMeta"></div>
      <div class="phase-dots" id="phaseDots"></div>
      <div class="criteria" id="acceptanceCriteria">No task selected.</div>
    </div>
    <div class="section">
      <h2>Controls</h2>
      <div class="controls">
        <button id="pauseBtn">Pause</button>
        <button id="resumeBtn">Resume</button>
        <button id="skipBtn" disabled>Skip</button>
        <button id="forcePassBtn" disabled>Force Pass</button>
      </div>
    </div>
    <div class="section">
      <h2>Cost Breakdown</h2>
      <div class="cost-grid" id="costBreakdown"></div>
    </div>
    <div class="section">
      <h2>Attempt History</h2>
      <div class="attempts" id="attemptHistory"></div>
    </div>
    <div class="event-log" id="eventLog"></div>
  </div>
</div>
<script>
const statusMap = {
  task_started: "in_progress",
  task_completed: "completed",
  task_failed: "failed",
  task_skipped: "skipped",
  task_retrying: "in_progress"
};
const tasks = {};
let selectedTask = null;

function esc(value) {
  const div = document.createElement("div");
  div.textContent = value || "";
  return div.innerHTML;
}

function computeLayers(taskMap) {
  const depth = {};
  function getDepth(id) {
    if (depth[id] !== undefined) return depth[id];
    const task = taskMap[id];
    if (!task || !task.depends_on || task.depends_on.length === 0) {
      depth[id] = 0;
      return 0;
    }
    depth[id] = 1 + Math.max(...task.depends_on.map(dep => getDepth(dep)));
    return depth[id];
  }
  Object.keys(taskMap).forEach(id => getDepth(Number(id)));
  const layers = {};
  Object.entries(depth).forEach(([id, layer]) => {
    if (!layers[layer]) layers[layer] = [];
    layers[layer].push(Number(id));
  });
  return layers;
}

function renderStats() {
  const all = Object.values(tasks);
  const counts = { completed: 0, failed: 0, in_progress: 0, pending: 0, skipped: 0 };
  all.forEach(task => { counts[task.status] = (counts[task.status] || 0) + 1; });
  document.getElementById("stats").innerHTML =
    "<span>done " + counts.completed + "</span>" +
    "<span>fail " + counts.failed + "</span>" +
    "<span>run " + counts.in_progress + "</span>" +
    "<span>wait " + (counts.pending + counts.skipped) + "</span>";
}

function renderDag() {
  const panel = document.getElementById("dagPanel");
  const layers = computeLayers(tasks);
  const maxLayer = Math.max(...Object.keys(layers).map(Number), 0);
  let html = "";
  for (let layer = 0; layer <= maxLayer; layer += 1) {
    const ids = (layers[layer] || []).sort((a, b) => a - b);
    html += '<div class="dag-layer">';
    ids.forEach(id => {
      const task = tasks[id];
      const phase = task.phase || "pending";
      const score = task.last_score !== undefined && task.last_score !== null
        ? "score " + task.last_score + "/100"
        : "score -";
      html +=
        '<div class="node ' + task.status + (selectedTask === id ? ' selected' : '') + '" onclick="selectTask(' + id + ')">' +
          '<div class="node-top">' +
            '<span>T' + id + ' [' + esc(task.module) + ']</span>' +
            '<span>' + esc(score) + '</span>' +
          '</div>' +
          '<div class="node-desc">' + esc(task.description) + '</div>' +
          '<div class="node-phase">phase ' + esc(phase) + '</div>' +
          '<div class="node-meta">deps ' + esc((task.depends_on || []).join(", ") || "none") + '</div>' +
        '</div>';
    });
    html += "</div>";
  }
  panel.innerHTML = html;
}

function renderCosts(task) {
  const target = document.getElementById("costBreakdown");
  const attempts = task ? (task.attempts || []) : [];
  let generator = 0;
  let evaluator = 0;
  attempts.forEach(record => {
    generator += (record.generator_output?.cost_usd || 0);
    evaluator += (record.eval_result?.cost_usd || 0);
  });
  const total = task ? (task.total_cost_usd || generator + evaluator) : 0;
  target.innerHTML =
    '<div class="cost-card"><div>Generator</div><strong>$' + generator.toFixed(2) + '</strong></div>' +
    '<div class="cost-card"><div>Evaluator</div><strong>$' + evaluator.toFixed(2) + '</strong></div>' +
    '<div class="cost-card"><div>Total</div><strong>$' + total.toFixed(2) + '</strong></div>' +
    '<div class="cost-card"><div>Attempts</div><strong>' + attempts.length + '</strong></div>';
}

function renderAttempts(task) {
  const target = document.getElementById("attemptHistory");
  if (!task || !task.attempts || task.attempts.length === 0) {
    target.innerHTML = '<div class="attempt-row"><div>-</div><div>-</div><div>No attempts yet.</div></div>';
    return;
  }
  target.innerHTML = task.attempts.map(record => {
    const summary = record.eval_result?.feedback?.summary || "No feedback";
    const score = record.eval_result?.score ?? "-";
    const duration = (record.generator_output?.duration_seconds || 0) + (record.eval_result?.duration_seconds || 0);
    const cost = (record.generator_output?.cost_usd || 0) + (record.eval_result?.cost_usd || 0);
    return '<div class="attempt-row">' +
      '<div>#' + record.attempt + '</div>' +
      '<div>' + score + '</div>' +
      '<div>' + esc(summary) + '<br><span style="color:var(--text-dim)">duration ' + duration.toFixed(1) + 's, cost $' + cost.toFixed(2) + '</span></div>' +
      '</div>';
  }).join("");
}

function renderPhases(task) {
  const target = document.getElementById("phaseDots");
  const phase = task ? (task.phase || "pending") : "pending";
  const active = phase.startsWith("evaluat")
    ? "evaluate"
    : phase.startsWith("generat")
      ? "generate"
      : phase === "done"
        ? "done"
        : "plan";
  target.innerHTML = ["plan", "generate", "evaluate", "done"].map(name =>
    '<div class="phase-dot ' + name + (active === name ? ' active ' + name : '') + '"></div>'
  ).join("");
}

function syncControls(task) {
  document.getElementById("skipBtn").disabled = !task;
  document.getElementById("forcePassBtn").disabled = !task;
}

function selectTask(id) {
  selectedTask = id;
  const task = tasks[id];
  document.getElementById("taskTitle").textContent = "T" + id + ": " + task.description;
  document.getElementById("taskMeta").textContent = "module " + task.module + " | status " + task.status + " | phase " + (task.phase || "pending");
  document.getElementById("acceptanceCriteria").textContent = task.acceptance_criteria || "No acceptance criteria.";
  renderPhases(task);
  renderAttempts(task);
  renderCosts(task);
  syncControls(task);
  renderDag();
}

async function sendCommand(path) {
  await fetch(path, { method: "POST" });
}

document.getElementById("pauseBtn").addEventListener("click", () => sendCommand("/api/pause"));
document.getElementById("resumeBtn").addEventListener("click", () => sendCommand("/api/resume"));
document.getElementById("skipBtn").addEventListener("click", () => {
  if (selectedTask) sendCommand("/api/task/" + selectedTask + "/skip");
});
document.getElementById("forcePassBtn").addEventListener("click", () => {
  if (selectedTask) sendCommand("/api/task/" + selectedTask + "/force-pass");
});

function addEventLine(text) {
  const log = document.getElementById("eventLog");
  const row = document.createElement("div");
  row.textContent = text;
  log.prepend(row);
  while (log.children.length > 80) log.removeChild(log.lastChild);
}

const source = new EventSource("/events");
source.addEventListener("init", event => {
  const data = JSON.parse(event.data);
  document.getElementById("goal").textContent = data.goal;
  data.tasks.forEach(task => {
    task.last_score = task.attempts?.length ? task.attempts[task.attempts.length - 1].eval_result?.score : null;
    tasks[task.id] = task;
  });
  renderStats();
  renderDag();
  renderPhases(null);
  renderAttempts(null);
  renderCosts(null);
  syncControls(null);
});

source.addEventListener("hook", event => {
  const hook = JSON.parse(event.data);
  const task = hook.task_id ? tasks[hook.task_id] : null;
  if (task && statusMap[hook.type]) task.status = statusMap[hook.type];
  if (task && hook.type === "phase_changed") task.phase = hook.data?.phase || task.phase;
  if (task && hook.type === "eval_scored") {
    task.phase = "evaluating";
    task.last_score = hook.data?.score;
    const attempt = hook.data?.attempt || 1;
    task.attempts = task.attempts || [];
    const existing = task.attempts.find(item => item.attempt === attempt);
    if (existing) {
      existing.eval_result = existing.eval_result || {};
      existing.eval_result.score = hook.data?.score || 0;
      existing.eval_result.passed = hook.data?.passed || false;
    } else {
      task.attempts.push({
        attempt: attempt,
        generator_output: { cost_usd: 0, duration_seconds: 0 },
        eval_result: {
          score: hook.data?.score || 0,
          passed: hook.data?.passed || false,
          feedback: { summary: "" },
          cost_usd: 0,
          duration_seconds: 0
        }
      });
    }
  }
  if (selectedTask && task && selectedTask === task.id) selectTask(task.id);
  renderStats();
  renderDag();
  addEventLine(hook.type + (hook.task_id ? " T" + hook.task_id : ""));
});
</script>
</body>
</html>
"""


def _state_payload(state: TaskState) -> dict[str, Any]:
    return {
        "id": state.id,
        "module": state.module,
        "description": state.description,
        "status": state.status.value,
        "depends_on": state.depends_on,
        "acceptance_criteria": state.acceptance_criteria,
        "phase": state.phase,
        "total_cost_usd": state.cost_usd,
        "attempts": [
            {
                "attempt": record.attempt,
                "timestamp": record.timestamp,
                "generator_output": {
                    "success": record.generator_output.success,
                    "output": record.generator_output.output,
                    "diff": record.generator_output.diff,
                    "cost_usd": record.generator_output.cost_usd,
                    "duration_seconds": record.generator_output.duration_seconds,
                    "event_count": record.generator_output.event_count,
                    "last_tool": record.generator_output.last_tool,
                },
                "eval_result": {
                    "score": record.eval_result.score,
                    "passed": record.eval_result.passed,
                    "retryable": record.eval_result.retryable,
                    "feedback": {
                        "summary": record.eval_result.feedback.summary,
                        "failed_criteria": record.eval_result.feedback.failed_criteria,
                        "evidence": record.eval_result.feedback.evidence,
                    },
                    "cost_usd": record.eval_result.cost_usd,
                    "duration_seconds": record.eval_result.duration_seconds,
                },
            }
            for record in state.attempts
        ],
    }


class _Handler(BaseHTTPRequestHandler):
    """HTTP handler for the web dashboard."""

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        """Suppress default stderr logging."""

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/":
            self._respond(200, "text/html; charset=utf-8", _INDEX_HTML)
        elif self.path == "/events":
            self._serve_sse()
        elif self.path == "/health":
            self._respond_json(200, {"ok": True})
        else:
            self._respond(404, "text/plain", "Not Found")

    def do_POST(self) -> None:  # noqa: N802
        queue_obj: CommandQueue | None = self.server.command_queue  # type: ignore[attr-defined]
        if queue_obj is None:
            self._respond_json(503, {"ok": False, "error": "command queue unavailable"})
            return

        if self.path == "/api/pause":
            queue_obj.pause()
            self._respond_json(200, {"ok": True})
            return
        if self.path == "/api/resume":
            queue_obj.resume()
            self._respond_json(200, {"ok": True})
            return

        match = re.fullmatch(r"/api/task/(\d+)/(skip|force-pass)", self.path)
        if match:
            task_id = int(match.group(1))
            action = match.group(2)
            if action == "skip":
                queue_obj.skip(task_id)
            else:
                queue_obj.force_pass(task_id)
            self._respond_json(200, {"ok": True})
            return

        self._respond_json(404, {"ok": False, "error": "not found"})

    def _respond(self, code: int, content_type: str, body: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body.encode())

    def _respond_json(self, code: int, payload: dict[str, Any]) -> None:
        self._respond(code, "application/json", json.dumps(payload))

    def _serve_sse(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        result: ExecutionResult = self.server.execution  # type: ignore[attr-defined]
        self._write_sse(
            "init",
            {
                "goal": result.resolved_goal,
                "tasks": [_state_payload(state) for state in result.states],
            },
        )

        client_queue: queue.Queue[dict[str, Any] | None] = queue.Queue()
        with self.server.queues_lock:  # type: ignore[attr-defined]
            self.server.event_queues.append(client_queue)  # type: ignore[attr-defined]
        try:
            while True:
                msg = client_queue.get()
                if msg is None:
                    break
                self._write_sse("hook", msg)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            with self.server.queues_lock:  # type: ignore[attr-defined]
                if client_queue in self.server.event_queues:  # type: ignore[attr-defined]
                    self.server.event_queues.remove(client_queue)  # type: ignore[attr-defined]

    def _write_sse(self, event: str, data: dict[str, Any]) -> None:
        payload = f"event: {event}\ndata: {json.dumps(data)}\n\n"
        self.wfile.write(payload.encode())
        self.wfile.flush()


class WebDashboard:
    """Browser-based dashboard that streams pipeline events over SSE."""

    def __init__(
        self,
        plan: TaskPlan | ExecutionResult | list[TaskState],
        hooks: HookRegistry,
        metrics_collector: Any | None = None,
        command_queue: CommandQueue | None = None,
        port: int = 8420,
    ) -> None:
        self._execution = coerce_execution_result(plan)
        self._hooks = hooks
        self._metrics_collector = metrics_collector
        self._command_queue = command_queue
        self._port = port
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def port(self) -> int:
        return self._port

    @property
    def url(self) -> str:
        return f"http://localhost:{self._port}"

    def start(self) -> None:
        """Start the HTTP server in a daemon thread and subscribe to hooks."""
        server = ThreadingHTTPServer(("127.0.0.1", self._port), _Handler)
        server.execution = self._execution  # type: ignore[attr-defined]
        server.command_queue = self._command_queue  # type: ignore[attr-defined]
        server.event_queues = []  # type: ignore[attr-defined]
        server.queues_lock = threading.Lock()  # type: ignore[attr-defined]
        self._server = server

        self._hooks.on_any(self._on_event)

        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self._thread = thread

    def stop(self) -> None:
        self._hooks.remove_any(self._on_event)
        if self._server is not None:
            with self._server.queues_lock:  # type: ignore[attr-defined]
                for client_queue in self._server.event_queues:  # type: ignore[attr-defined]
                    client_queue.put(None)
            self._server.shutdown()
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def _on_event(self, event: Event) -> None:
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
            for client_queue in self._server.event_queues:  # type: ignore[attr-defined]
                client_queue.put(data)
