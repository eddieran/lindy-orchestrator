"""DAG visualization for TaskPlan.

Renders a task dependency graph as a compact ASCII tree with box-drawing
characters inspired by Elixir's supervisor tree visualization.  Each task
node shows a status icon, task ID, module name, and a short description.
Optional annotation bubbles (``← message``) show latest activity next to
active or recently-completed tasks.
"""

from __future__ import annotations

from rich.text import Text

from lindy_orchestrator.models import TaskSpec, TaskPlan, TaskStatus

# -- text helpers -------------------------------------------------------------


def truncate_goal(text: str, max_chars: int = 72) -> str:
    """Collapse whitespace and truncate *text* with head…tail ellipsis.

    If the collapsed text fits within *max_chars*, it is returned as-is.
    Otherwise, the first 60 % and last 30 % are kept with " … " in between.
    """
    import re

    collapsed = re.sub(r"\s+", " ", text).strip()
    if len(collapsed) <= max_chars:
        return collapsed
    head_len = int(max_chars * 0.6)
    tail_len = int(max_chars * 0.3)
    return collapsed[:head_len] + " \u2026 " + collapsed[-tail_len:]


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

_MAX_WIDTH = 78  # content width (80 minus 2-char margin)

# -- topology -----------------------------------------------------------------


def _compute_levels(tasks: list[TaskSpec]) -> list[list[TaskSpec]]:
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
    levels: list[list[TaskSpec]] = [[] for _ in range(n_levels)]
    for t in tasks:
        levels[memo[t.id]].append(t)
    return levels


# -- tree building ------------------------------------------------------------


def _build_tree(
    tasks: list[TaskSpec],
) -> tuple[list[TaskSpec], dict[int, list[TaskSpec]], dict[int, list[int]]]:
    """Build a tree structure from the task DAG for rendering.

    Each task is assigned to exactly one tree parent (the dependency at the
    highest topological level; ties broken by highest task ID).  Tasks with
    no dependencies become roots.

    Returns:
        roots: Root tasks (no valid dependencies).
        children: Mapping task_id -> list of child tasks in tree order.
        extra_deps: Mapping task_id -> additional parent IDs not shown in tree.
    """
    if not tasks:
        return [], {}, {}

    task_map = {t.id: t for t in tasks}
    levels = _compute_levels(tasks)
    level_of: dict[int, int] = {}
    for i, level_tasks in enumerate(levels):
        for t in level_tasks:
            level_of[t.id] = i

    children: dict[int, list[TaskSpec]] = {t.id: [] for t in tasks}
    extra_deps: dict[int, list[int]] = {}
    roots: list[TaskSpec] = []

    for t in tasks:
        valid_deps = [d for d in t.depends_on if d in task_map]
        if not valid_deps:
            roots.append(t)
        else:
            primary = max(valid_deps, key=lambda d: (level_of.get(d, 0), d))
            children[primary].append(t)
            others = sorted(d for d in valid_deps if d != primary)
            if others:
                extra_deps[t.id] = others

    # Stable ordering by task ID
    roots.sort(key=lambda t: t.id)
    for tid in children:
        children[tid].sort(key=lambda t: t.id)

    return roots, children, extra_deps


# -- node formatting ----------------------------------------------------------


def _node_text(task: TaskSpec, extra_deps: dict[int, list[int]]) -> str:
    """Build the display text for a single task node."""
    icon = STATUS_ICONS[task.status]
    desc = task.description
    if len(desc) > 30:
        desc = desc[:29] + "\u2026"
    extra = extra_deps.get(task.id, [])
    dep_note = f" [+{','.join(str(d) for d in extra)}]" if extra else ""
    return f"{icon} {task.id} {task.module}: {desc}{dep_note}"


def _format_line(
    prefix: str,
    connector: str,
    node: str,
    annotation: str = "",
    max_width: int = _MAX_WIDTH,
) -> str:
    """Format a single tree line, ensuring it fits within *max_width*."""
    line = f"{prefix}{connector}{node}"

    if annotation:
        ann = f" \u2190 {annotation}"  # ← annotation
        if len(line) + len(ann) > max_width:
            avail = max_width - len(line) - 5  # " ← " + "…"
            if avail > 0:
                ann = f" \u2190 {annotation[:avail]}\u2026"
            else:
                ann = ""
        line += ann

    if len(line) > max_width:
        line = line[: max_width - 1] + "\u2026"
    return line


# -- tree walking -------------------------------------------------------------


def _walk_tree(
    tasks: list[TaskSpec],
    annotations: dict[int, str] | None = None,
    verbose: bool = False,
) -> list[tuple[str, str, str, str, str, TaskSpec]]:
    """Walk the task DAG tree and yield rendering tuples.

    Returns a list of ``(prefix, connector, node, annotation, style, task)``
    tuples in display order.
    """
    if not tasks:
        return [("", "", "(empty plan)", "", "dim", None)]  # type: ignore[list-item]

    roots, children, extra_deps = _build_tree(tasks)
    annotations = annotations or {}
    result: list[tuple[str, str, str, str, str, TaskSpec]] = []

    def _emit(task: TaskSpec, prefix: str, is_last: bool) -> None:
        connector = "\u2514\u2500\u2500 " if is_last else "\u251c\u2500\u2500 "
        node = _node_text(task, extra_deps)
        ann = ""
        if verbose and task.id in annotations and annotations[task.id]:
            ann = annotations[task.id]
            if len(ann) > 40:
                ann = ann[:39] + "\u2026"

        result.append((prefix, connector, node, ann, STATUS_STYLES[task.status], task))

        kids = children.get(task.id, [])
        child_prefix = prefix + ("    " if is_last else "\u2502   ")
        for i, child in enumerate(kids):
            _emit(child, child_prefix, i == len(kids) - 1)

    for i, root in enumerate(roots):
        _emit(root, "", i == len(roots) - 1)

    return result


# -- public API ---------------------------------------------------------------


def render_dag(
    plan: TaskPlan,
    annotations: dict[int, str] | None = None,
    verbose: bool = False,
) -> Text:
    """Render a TaskPlan DAG as a :class:`rich.text.Text` renderable.

    Nodes are coloured by status; tree connectors use dim box-drawing
    characters.  When *verbose* is True and *annotations* are provided,
    activity bubbles appear next to active nodes.
    """
    text = Text()

    if not plan.tasks:
        text.append("(empty plan)", style="dim")
        return text

    text.append(f"DAG: {truncate_goal(plan.goal)}\n", style="bold")

    for prefix, connector, node, ann, style, _task in _walk_tree(plan.tasks, annotations, verbose):
        text.append(prefix, style="dim")
        text.append(connector, style="dim")
        text.append(node, style=style)
        if ann:
            # Compute padding so annotations align
            current_len = len(prefix) + len(connector) + len(node)
            pad = max(52 - current_len, 1)
            # Truncate annotation to fit _MAX_WIDTH
            avail = _MAX_WIDTH - current_len - pad - 2  # 2 for "← "
            if avail > 0:
                display_ann = ann[:avail] if len(ann) > avail else ann
                text.append(" " * pad, style="dim")
                text.append(f"\u2190 {display_ann}", style="dim italic")
        text.append("\n")

    return text


def render_dag_ascii(
    plan: TaskPlan,
    annotations: dict[int, str] | None = None,
    verbose: bool = False,
) -> str:
    """Render a TaskPlan DAG as plain ASCII / Unicode text (no colour)."""
    if not plan.tasks:
        return "(empty plan)"

    lines: list[str] = [f"DAG: {truncate_goal(plan.goal)}"]

    for prefix, connector, node, ann, _style, _task in _walk_tree(plan.tasks, annotations, verbose):
        lines.append(_format_line(prefix, connector, node, ann))

    return "\n".join(lines)
