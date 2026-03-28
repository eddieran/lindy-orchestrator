"""Tests for DAG visualization module (compact tree rendering)."""

from rich.text import Text

from lindy_orchestrator.dag import (
    STATUS_ICONS,
    STATUS_STYLES,
    _build_tree,
    _compute_levels,
    render_dag,
    render_dag_ascii,
)
from lindy_orchestrator.models import TaskSpec, TaskPlan, TaskStatus


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _plan(*tasks: TaskSpec, goal: str = "test") -> TaskPlan:
    return TaskPlan(goal=goal, tasks=list(tasks))


def _task(tid: int, module: str = "mod", desc: str = "do thing", **kw) -> TaskSpec:
    return TaskSpec(id=tid, module=module, description=desc, **kw)


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
# Tree building
# ---------------------------------------------------------------------------


class TestBuildTree:
    def test_empty(self):
        roots, children, extra = _build_tree([])
        assert roots == []
        assert children == {}
        assert extra == {}

    def test_single_root(self):
        t = _task(1)
        roots, children, extra = _build_tree([t])
        assert roots == [t]
        assert children[1] == []
        assert extra == {}

    def test_linear_chain_tree(self):
        t1 = _task(1)
        t2 = _task(2, depends_on=[1])
        t3 = _task(3, depends_on=[2])
        roots, children, extra = _build_tree([t1, t2, t3])
        assert roots == [t1]
        assert children[1] == [t2]
        assert children[2] == [t3]

    def test_diamond_assigns_to_deepest_parent(self):
        t1 = _task(1)
        t2 = _task(2, depends_on=[1])
        t3 = _task(3, depends_on=[1])
        t4 = _task(4, depends_on=[2, 3])
        roots, children, extra = _build_tree([t1, t2, t3, t4])
        assert roots == [t1]
        # t4 assigned to highest-level parent (both at level 1, highest id = 3)
        assert t4 in children[3]
        assert 2 in extra[4]

    def test_multiple_roots(self):
        t1 = _task(1)
        t2 = _task(2)
        roots, children, extra = _build_tree([t1, t2])
        assert len(roots) == 2
        assert roots[0].id == 1
        assert roots[1].id == 2

    def test_children_sorted_by_id(self):
        t1 = _task(1)
        t3 = _task(3, depends_on=[1])
        t2 = _task(2, depends_on=[1])
        roots, children, extra = _build_tree([t1, t3, t2])
        assert [c.id for c in children[1]] == [2, 3]


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
# Tree rendering — single task
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
# Tree rendering — linear chain
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
        assert lines[0] == "DAG: Chain"
        assert "1 a" in out
        assert "2 b" in out
        assert "3 c" in out
        # Tree uses box-drawing connectors
        assert "\u2514" in out or "\u251c" in out  # └ or ├

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
# Tree rendering — diamond dependency pattern
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
        for tid in range(1, 5):
            assert f"{tid} " in out
        # Diamond merge task should show extra dependency
        assert "[+" in out  # extra dep annotation like [+2]

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
# Tree rendering — wide parallel fan-out
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
        for tid in range(1, 6):
            assert f"{tid} " in out
        # Tree connectors present
        assert "\u251c" in out  # ├
        assert "\u2514" in out  # └

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
