"""DAG visualization for TaskPlan.

Renders a task dependency graph as either a Rich renderable (for terminal Live
display) or plain ASCII (for logging / CI).  Tasks at the same topological
level are placed side-by-side on the same row, with box-drawing edges between
rows showing dependency relationships.
"""

from __future__ import annotations

from rich.text import Text

from lindy_orchestrator.models import TaskItem, TaskPlan, TaskStatus

# -- status display -----------------------------------------------------------

STATUS_ICONS: dict[TaskStatus, str] = {
    TaskStatus.PENDING: "\u2b21",  # ⬡
    TaskStatus.IN_PROGRESS: "\u25c9",  # ◉
    TaskStatus.COMPLETED: "\u2713",  # ✓
    TaskStatus.FAILED: "\u2717",  # ✗
    TaskStatus.SKIPPED: "\u25cb",  # ○
}

STATUS_STYLES: dict[TaskStatus, str] = {
    TaskStatus.PENDING: "dim",
    TaskStatus.IN_PROGRESS: "bold blue",
    TaskStatus.COMPLETED: "green",
    TaskStatus.FAILED: "bold red",
    TaskStatus.SKIPPED: "dim",
}

# -- layout constants ---------------------------------------------------------

_CELL_W = 30  # fixed width per task cell
_GAP = 4  # gap between adjacent cells

# -- direction flags for box-drawing ------------------------------------------

_UP, _DOWN, _LEFT, _RIGHT = 1, 2, 4, 8

_BOX: dict[int, str] = {
    _UP: "\u2502",
    _DOWN: "\u2502",
    _LEFT: "\u2500",
    _RIGHT: "\u2500",
    _UP | _DOWN: "\u2502",
    _LEFT | _RIGHT: "\u2500",
    _UP | _RIGHT: "\u2514",
    _UP | _LEFT: "\u2518",
    _DOWN | _RIGHT: "\u250c",
    _DOWN | _LEFT: "\u2510",
    _UP | _LEFT | _RIGHT: "\u2534",
    _DOWN | _LEFT | _RIGHT: "\u252c",
    _UP | _DOWN | _RIGHT: "\u251c",
    _UP | _DOWN | _LEFT: "\u2524",
    _UP | _DOWN | _LEFT | _RIGHT: "\u253c",
}

# -- topology -----------------------------------------------------------------


def _compute_levels(tasks: list[TaskItem]) -> list[list[TaskItem]]:
    """Group tasks into topological levels.

    Level 0 contains tasks with no (valid) dependencies.  Level *n* contains
    tasks whose deepest dependency is at level *n-1*.
    """
    if not tasks:
        return []

    task_map = {t.id: t for t in tasks}
    memo: dict[int, int] = {}

    def depth(tid: int) -> int:
        if tid in memo:
            return memo[tid]
        t = task_map[tid]
        parents = [d for d in t.depends_on if d in task_map]
        memo[tid] = 0 if not parents else max(depth(d) for d in parents) + 1
        return memo[tid]

    for t in tasks:
        depth(t.id)

    n_levels = max(memo.values()) + 1
    levels: list[list[TaskItem]] = [[] for _ in range(n_levels)]
    for t in tasks:
        levels[memo[t.id]].append(t)
    return levels


# -- grid helpers -------------------------------------------------------------


def _center_col(idx: int) -> int:
    """Column-center of the *idx*-th cell in a level row."""
    return idx * (_CELL_W + _GAP) + _CELL_W // 2


def _node_label(task: TaskItem) -> str:
    """Plain-text label for a single task node."""
    icon = STATUS_ICONS[task.status]
    head = f"{icon} {task.id} {task.module}"
    room = _CELL_W - len(head) - 2  # ": "
    if room > 3:
        desc = task.description
        if len(desc) > room:
            desc = desc[: room - 1] + "\u2026"
        return f"{head}: {desc}"
    return head[:_CELL_W]


# -- level-row rendering ------------------------------------------------------


def _level_line_ascii(level: list[TaskItem]) -> str:
    parts: list[str] = []
    for i, t in enumerate(level):
        label = _node_label(t)
        parts.append(label[:_CELL_W].ljust(_CELL_W))
        if i < len(level) - 1:
            parts.append(" " * _GAP)
    return "".join(parts)


def _level_line_rich(level: list[TaskItem]) -> Text:
    text = Text()
    for i, t in enumerate(level):
        label = _node_label(t)
        padded = label[:_CELL_W].ljust(_CELL_W)
        text.append(padded, style=STATUS_STYLES[t.status])
        if i < len(level) - 1:
            text.append(" " * _GAP)
    return text


# -- edge rendering -----------------------------------------------------------


def _edge_lines(
    prev_level: list[TaskItem],
    next_level: list[TaskItem],
) -> list[str]:
    """Compute box-drawing connector rows between two adjacent levels."""
    prev_pos = {t.id: _center_col(i) for i, t in enumerate(prev_level)}
    next_pos = {t.id: _center_col(i) for i, t in enumerate(next_level)}

    # Determine required grid width
    all_cols: list[int] = []
    if prev_pos:
        all_cols.extend(prev_pos.values())
    if next_pos:
        all_cols.extend(next_pos.values())
    if not all_cols:
        return []
    grid_w = max(all_cols) + 1

    # Direction mask per column
    dirs = [0] * grid_w

    for t in next_level:
        pcols = sorted({prev_pos[d] for d in t.depends_on if d in prev_pos})
        if not pcols:
            continue
        cc = next_pos[t.id]
        points = sorted(set(pcols + [cc]))
        lo, hi = points[0], points[-1]

        # Horizontal span
        for c in range(lo, hi + 1):
            if c > lo:
                dirs[c] |= _LEFT
            if c < hi:
                dirs[c] |= _RIGHT

        # Parent drops
        for pc in pcols:
            dirs[pc] |= _UP

        # Child arrival
        dirs[cc] |= _DOWN

    if not any(dirs):
        return []

    drop: list[str] = []
    merge: list[str] = []
    arrive: list[str] = []

    for c in range(grid_w):
        d = dirs[c]
        drop.append("\u2502" if d & _UP else " ")
        merge.append(_BOX.get(d, " ") if d else " ")
        arrive.append("\u25bc" if d & _DOWN else " ")

    return [
        "".join(drop).rstrip(),
        "".join(merge).rstrip(),
        "".join(arrive).rstrip(),
    ]


# -- public API ---------------------------------------------------------------


def render_dag(plan: TaskPlan) -> Text:
    """Render a TaskPlan DAG as a :class:`rich.text.Text` renderable.

    Nodes are coloured by status; edges use dim box-drawing characters.
    """
    text = Text()

    if not plan.tasks:
        text.append("(empty plan)", style="dim")
        return text

    text.append(f"DAG: {plan.goal}\n", style="bold")

    levels = _compute_levels(plan.tasks)
    for i, level in enumerate(levels):
        text.append_text(_level_line_rich(level))
        text.append("\n")
        if i < len(levels) - 1:
            for line in _edge_lines(level, levels[i + 1]):
                text.append(line + "\n", style="dim")

    return text


def render_dag_ascii(plan: TaskPlan) -> str:
    """Render a TaskPlan DAG as plain ASCII / Unicode text (no colour)."""
    if not plan.tasks:
        return "(empty plan)"

    lines: list[str] = [f"DAG: {plan.goal}"]

    levels = _compute_levels(plan.tasks)
    for i, level in enumerate(levels):
        lines.append(_level_line_ascii(level))
        if i < len(levels) - 1:
            lines.extend(_edge_lines(level, levels[i + 1]))

    return "\n".join(lines)
