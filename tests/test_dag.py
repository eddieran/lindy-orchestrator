"""Tests for DAG visualization module."""

from rich.text import Text

from lindy_orchestrator.dag import (
    STATUS_ICONS,
    STATUS_STYLES,
    _compute_levels,
    _edge_lines,
    render_dag,
    render_dag_ascii,
)
from lindy_orchestrator.models import TaskItem, TaskPlan, TaskStatus


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _plan(*tasks: TaskItem, goal: str = "test") -> TaskPlan:
    return TaskPlan(goal=goal, tasks=list(tasks))


def _task(tid: int, module: str = "mod", desc: str = "do thing", **kw) -> TaskItem:
    return TaskItem(id=tid, module=module, description=desc, **kw)


# ---------------------------------------------------------------------------
# Topology sorting
# ---------------------------------------------------------------------------


class TestComputeLevels:
    def test_empty(self):
        assert _compute_levels([]) == []

    def test_single_task(self):
        t = _task(1)
        levels = _compute_levels([t])
        assert len(levels) == 1
        assert levels[0] == [t]

    def test_no_deps_all_level_zero(self):
        tasks = [_task(1), _task(2), _task(3)]
        levels = _compute_levels(tasks)
        assert len(levels) == 1
        assert len(levels[0]) == 3

    def test_linear_chain(self):
        t1 = _task(1)
        t2 = _task(2, depends_on=[1])
        t3 = _task(3, depends_on=[2])
        levels = _compute_levels([t1, t2, t3])
        assert len(levels) == 3
        assert levels[0] == [t1]
        assert levels[1] == [t2]
        assert levels[2] == [t3]

    def test_diamond(self):
        t1 = _task(1)
        t2 = _task(2, depends_on=[1])
        t3 = _task(3, depends_on=[1])
        t4 = _task(4, depends_on=[2, 3])
        levels = _compute_levels([t1, t2, t3, t4])
        assert len(levels) == 3
        assert levels[0] == [t1]
        assert {t.id for t in levels[1]} == {2, 3}
        assert levels[2] == [t4]

    def test_wide_fanout(self):
        root = _task(1)
        children = [_task(i, depends_on=[1]) for i in range(2, 7)]
        levels = _compute_levels([root] + children)
        assert len(levels) == 2
        assert levels[0] == [root]
        assert len(levels[1]) == 5

    def test_ignores_unknown_deps(self):
        """Dependencies referencing non-existent task IDs are ignored."""
        t1 = _task(1, depends_on=[99])
        levels = _compute_levels([t1])
        assert len(levels) == 1
        assert levels[0] == [t1]


# ---------------------------------------------------------------------------
# Status icons & coloring
# ---------------------------------------------------------------------------


class TestStatusDisplay:
    def test_all_statuses_have_icons(self):
        for status in TaskStatus:
            assert status in STATUS_ICONS

    def test_all_statuses_have_styles(self):
        for status in TaskStatus:
            assert status in STATUS_STYLES

    def test_rich_render_applies_correct_styles(self):
        plan = _plan(
            _task(1, status=TaskStatus.COMPLETED),
            _task(2, status=TaskStatus.FAILED),
            _task(3, status=TaskStatus.IN_PROGRESS),
        )
        result = render_dag(plan)
        assert isinstance(result, Text)

        plain = result.plain
        assert STATUS_ICONS[TaskStatus.COMPLETED] in plain
        assert STATUS_ICONS[TaskStatus.FAILED] in plain
        assert STATUS_ICONS[TaskStatus.IN_PROGRESS] in plain

        # Check that styled spans exist for the expected styles
        style_strs = {str(span.style) for span in result._spans}
        assert STATUS_STYLES[TaskStatus.COMPLETED] in style_strs
        assert STATUS_STYLES[TaskStatus.FAILED] in style_strs
        assert STATUS_STYLES[TaskStatus.IN_PROGRESS] in style_strs

    def test_pending_icon_dim(self):
        plan = _plan(_task(1, status=TaskStatus.PENDING))
        result = render_dag(plan)
        assert STATUS_ICONS[TaskStatus.PENDING] in result.plain
        style_strs = {str(span.style) for span in result._spans}
        assert "dim" in style_strs

    def test_skipped_icon_dim(self):
        plan = _plan(_task(1, status=TaskStatus.SKIPPED))
        result = render_dag(plan)
        assert STATUS_ICONS[TaskStatus.SKIPPED] in result.plain


# ---------------------------------------------------------------------------
# Edge rendering
# ---------------------------------------------------------------------------


class TestEdgeRendering:
    def test_no_edges_for_single_level(self):
        """Tasks with no dependencies produce no edge rows."""
        level = [_task(1), _task(2)]
        result = _edge_lines(level, [])
        assert result == []

    def test_direct_vertical(self):
        """Parent directly above child → vertical connector."""
        parent_level = [_task(1)]
        child_level = [_task(2, depends_on=[1])]
        rows = _edge_lines(parent_level, child_level)
        assert len(rows) == 3
        # The merge row should contain │ (vertical pass-through)
        assert "\u2502" in rows[1]
        # The arrive row should contain ▼
        assert "\u25bc" in rows[2]

    def test_fan_out_edges(self):
        """Single parent fanning to multiple children."""
        parent_level = [_task(1)]
        child_level = [_task(2, depends_on=[1]), _task(3, depends_on=[1])]
        rows = _edge_lines(parent_level, child_level)
        assert len(rows) == 3
        # Should contain horizontal connector ─
        assert "\u2500" in rows[1]
        # Should have ▼ for both children
        assert rows[2].count("\u25bc") == 2

    def test_diamond_merge_edges(self):
        """Two parents merging into one child."""
        parent_level = [_task(1), _task(2)]
        child_level = [_task(3, depends_on=[1, 2])]
        rows = _edge_lines(parent_level, child_level)
        assert len(rows) == 3
        # Drop row: two │ for two parents
        assert rows[0].count("\u2502") == 2
        # Merge row: horizontal connector
        assert "\u2500" in rows[1]
        # Arrive row: one ▼ for the child
        assert rows[2].count("\u25bc") == 1

    def test_no_edges_when_no_deps(self):
        """Children without dependencies produce no connectors."""
        parent_level = [_task(1)]
        child_level = [_task(2)]  # no depends_on
        rows = _edge_lines(parent_level, child_level)
        assert rows == []


# ---------------------------------------------------------------------------
# Single-task plan
# ---------------------------------------------------------------------------


class TestSingleTask:
    def test_ascii(self):
        plan = _plan(_task(1, module="api", desc="Setup"), goal="Init")
        out = render_dag_ascii(plan)
        assert "DAG: Init" in out
        assert "1 api" in out
        assert "Setup" in out

    def test_rich(self):
        plan = _plan(_task(1, module="api", desc="Setup"), goal="Init")
        result = render_dag(plan)
        assert isinstance(result, Text)
        assert "1 api" in result.plain


# ---------------------------------------------------------------------------
# Linear chain
# ---------------------------------------------------------------------------


class TestLinearChain:
    def test_ascii_three_levels(self):
        plan = _plan(
            _task(1, module="a", desc="First"),
            _task(2, module="b", desc="Second", depends_on=[1]),
            _task(3, module="c", desc="Third", depends_on=[2]),
            goal="Chain",
        )
        out = render_dag_ascii(plan)
        lines = out.split("\n")
        # Header + 3 level rows + 2 * 3 edge rows = 10 lines
        assert lines[0] == "DAG: Chain"
        # Each level has exactly one task
        assert "1 a" in out
        assert "2 b" in out
        assert "3 c" in out
        # Vertical connectors between levels
        assert "\u25bc" in out  # ▼

    def test_rich_three_levels(self):
        plan = _plan(
            _task(1, module="a", desc="First"),
            _task(2, module="b", desc="Second", depends_on=[1]),
            _task(3, module="c", desc="Third", depends_on=[2]),
        )
        result = render_dag(plan)
        assert "1 a" in result.plain
        assert "3 c" in result.plain


# ---------------------------------------------------------------------------
# Diamond dependency pattern
# ---------------------------------------------------------------------------


class TestDiamondPattern:
    def test_ascii(self):
        plan = _plan(
            _task(1, module="core", desc="Init"),
            _task(2, module="fe", desc="Frontend", depends_on=[1]),
            _task(3, module="be", desc="Backend", depends_on=[1]),
            _task(4, module="qa", desc="Integrate", depends_on=[2, 3]),
            goal="Diamond",
        )
        out = render_dag_ascii(plan)
        assert "DAG: Diamond" in out
        # All tasks present
        for tid in range(1, 5):
            assert f"{tid} " in out
        # Edge connectors present
        assert "\u25bc" in out  # ▼
        assert "\u2502" in out  # │

    def test_rich(self):
        plan = _plan(
            _task(1, module="core", desc="Init"),
            _task(2, module="fe", desc="Frontend", depends_on=[1]),
            _task(3, module="be", desc="Backend", depends_on=[1]),
            _task(4, module="qa", desc="Integrate", depends_on=[2, 3]),
        )
        result = render_dag(plan)
        assert isinstance(result, Text)
        plain = result.plain
        assert "1 core" in plain
        assert "4 qa" in plain


# ---------------------------------------------------------------------------
# Wide parallel fan-out
# ---------------------------------------------------------------------------


class TestWideFanOut:
    def test_ascii(self):
        plan = _plan(
            _task(1, module="root", desc="Setup"),
            _task(2, module="a", desc="Worker A", depends_on=[1]),
            _task(3, module="b", desc="Worker B", depends_on=[1]),
            _task(4, module="c", desc="Worker C", depends_on=[1]),
            _task(5, module="d", desc="Worker D", depends_on=[1]),
            goal="FanOut",
        )
        out = render_dag_ascii(plan)
        assert "DAG: FanOut" in out
        # All 5 tasks present
        for tid in range(1, 6):
            assert f"{tid} " in out
        # Fan-out edges present
        assert "\u25bc" in out
        # 4 children → 4 ▼ arrows (plus one possible from root level)
        lines = out.split("\n")
        arrow_line = [ln for ln in lines if "\u25bc" in ln]
        assert len(arrow_line) >= 1
        # At least 4 ▼ in the arrow row(s)
        total_arrows = sum(ln.count("\u25bc") for ln in arrow_line)
        assert total_arrows >= 4

    def test_rich(self):
        plan = _plan(
            _task(1, module="root", desc="Setup"),
            _task(2, module="a", desc="Task A", depends_on=[1]),
            _task(3, module="b", desc="Task B", depends_on=[1]),
            _task(4, module="c", desc="Task C", depends_on=[1]),
            _task(5, module="d", desc="Task D", depends_on=[1]),
        )
        result = render_dag(plan)
        assert isinstance(result, Text)
        assert "5 d" in result.plain


# ---------------------------------------------------------------------------
# Empty plan
# ---------------------------------------------------------------------------


class TestEmptyPlan:
    def test_ascii_empty(self):
        plan = TaskPlan(goal="nothing", tasks=[])
        assert render_dag_ascii(plan) == "(empty plan)"

    def test_rich_empty(self):
        plan = TaskPlan(goal="nothing", tasks=[])
        result = render_dag(plan)
        assert "(empty plan)" in result.plain


# ---------------------------------------------------------------------------
# Mixed statuses
# ---------------------------------------------------------------------------


class TestMixedStatuses:
    def test_all_status_icons_appear(self):
        plan = _plan(
            _task(1, status=TaskStatus.COMPLETED),
            _task(2, status=TaskStatus.FAILED),
            _task(3, status=TaskStatus.IN_PROGRESS),
            _task(4, status=TaskStatus.PENDING),
            _task(5, status=TaskStatus.SKIPPED),
        )
        out = render_dag_ascii(plan)
        for status in TaskStatus:
            assert STATUS_ICONS[status] in out
