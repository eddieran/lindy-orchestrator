"""Tests for DAG rendering: annotations and terminal fit constraints."""

from lindy_orchestrator.dag import (
    STATUS_ICONS,
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
# Annotations (verbose mode)
# ---------------------------------------------------------------------------


class TestAnnotations:
    def test_annotations_shown_when_verbose(self):
        plan = _plan(
            _task(1, module="api", desc="Build", status=TaskStatus.IN_PROGRESS),
            _task(2, module="fe", desc="Frontend", depends_on=[1]),
        )
        annotations = {1: "tool: Edit"}
        out = render_dag_ascii(plan, annotations=annotations, verbose=True)
        assert "\u2190 tool: Edit" in out  # ← tool: Edit

    def test_annotations_hidden_when_not_verbose(self):
        plan = _plan(
            _task(1, module="api", desc="Build", status=TaskStatus.IN_PROGRESS),
        )
        annotations = {1: "tool: Edit"}
        out = render_dag_ascii(plan, annotations=annotations, verbose=False)
        assert "\u2190" not in out

    def test_long_annotation_truncated(self):
        plan = _plan(
            _task(1, module="api", desc="Build", status=TaskStatus.IN_PROGRESS),
        )
        annotations = {1: "x" * 100}
        out = render_dag_ascii(plan, annotations=annotations, verbose=True)
        # Should be truncated to fit within 78 chars
        lines = out.split("\n")
        for line in lines:
            assert len(line) <= 78, f"Line too long ({len(line)}): {line!r}"

    def test_rich_annotations(self):
        plan = _plan(
            _task(1, module="api", desc="Build", status=TaskStatus.IN_PROGRESS),
        )
        annotations = {1: "tool: Bash"}
        result = render_dag(plan, annotations=annotations, verbose=True)
        assert "\u2190 tool: Bash" in result.plain


# ---------------------------------------------------------------------------
# Terminal fit test (80 columns x 24 rows)
# ---------------------------------------------------------------------------


class TestFitsTerminal:
    def test_six_task_dag_fits_80x24(self):
        """6-task DAG with mixed statuses fits within 80 columns and 24 rows."""
        plan = _plan(
            _task(
                1,
                module="core",
                desc="Initialize project",
                status=TaskStatus.COMPLETED,
            ),
            _task(
                2,
                module="api",
                desc="Build API endpoints",
                status=TaskStatus.COMPLETED,
                depends_on=[1],
            ),
            _task(
                3,
                module="fe",
                desc="Build frontend UI",
                status=TaskStatus.IN_PROGRESS,
                depends_on=[1],
            ),
            _task(
                4,
                module="be",
                desc="Build backend service",
                status=TaskStatus.PENDING,
                depends_on=[1],
            ),
            _task(
                5,
                module="qa",
                desc="Run integration tests",
                status=TaskStatus.FAILED,
                depends_on=[2, 3, 4],
            ),
            _task(
                6,
                module="deploy",
                desc="Deploy to staging",
                status=TaskStatus.SKIPPED,
                depends_on=[5],
            ),
            goal="Deploy Application",
        )
        out = render_dag_ascii(plan)
        lines = out.strip().split("\n")
        assert len(lines) <= 24, f"DAG has {len(lines)} lines, must be <= 24"
        for i, line in enumerate(lines):
            assert len(line) <= 80, f"Line {i} is {len(line)} chars: {line!r}"

    def test_six_task_dag_with_annotations_fits_80x24(self):
        """6-task DAG with verbose annotations fits within 80 columns and 24 rows."""
        plan = _plan(
            _task(
                1,
                module="core",
                desc="Initialize project",
                status=TaskStatus.COMPLETED,
            ),
            _task(
                2,
                module="api",
                desc="Build API endpoints",
                status=TaskStatus.COMPLETED,
                depends_on=[1],
            ),
            _task(
                3,
                module="fe",
                desc="Build frontend UI",
                status=TaskStatus.IN_PROGRESS,
                depends_on=[1],
            ),
            _task(
                4,
                module="be",
                desc="Build backend service",
                status=TaskStatus.PENDING,
                depends_on=[1],
            ),
            _task(
                5,
                module="qa",
                desc="Run integration tests",
                status=TaskStatus.FAILED,
                depends_on=[2, 3, 4],
            ),
            _task(
                6,
                module="deploy",
                desc="Deploy to staging",
                status=TaskStatus.SKIPPED,
                depends_on=[5],
            ),
            goal="Deploy Application",
        )
        annotations = {
            1: "completed in 30s",
            2: "tool: Edit",
            3: "tool: Bash",
            5: "error: timeout after 120s",
        }
        out = render_dag_ascii(plan, annotations=annotations, verbose=True)
        lines = out.strip().split("\n")
        assert len(lines) <= 24, f"DAG has {len(lines)} lines, must be <= 24"
        for i, line in enumerate(lines):
            assert len(line) <= 80, f"Line {i} is {len(line)} chars: {line!r}"

    def test_eight_task_linear_fits_80x24(self):
        """8-task linear chain fits within 80 columns and 24 rows."""
        tasks = [_task(1, module="m1", desc="Step 1")]
        for i in range(2, 9):
            tasks.append(_task(i, module=f"m{i}", desc=f"Step {i}", depends_on=[i - 1]))
        plan = _plan(*tasks, goal="Linear Chain")
        out = render_dag_ascii(plan)
        lines = out.strip().split("\n")
        assert len(lines) <= 24, f"DAG has {len(lines)} lines, must be <= 24"
        for i, line in enumerate(lines):
            assert len(line) <= 80, f"Line {i} is {len(line)} chars: {line!r}"

    def test_six_task_dag_all_statuses_in_output(self):
        """Verify all status icons appear in the 6-task DAG output."""
        plan = _plan(
            _task(1, module="core", desc="Init", status=TaskStatus.COMPLETED),
            _task(2, module="api", desc="API", status=TaskStatus.COMPLETED, depends_on=[1]),
            _task(3, module="fe", desc="FE", status=TaskStatus.IN_PROGRESS, depends_on=[1]),
            _task(4, module="be", desc="BE", status=TaskStatus.PENDING, depends_on=[1]),
            _task(5, module="qa", desc="QA", status=TaskStatus.FAILED, depends_on=[2, 3, 4]),
            _task(6, module="deploy", desc="Deploy", status=TaskStatus.SKIPPED, depends_on=[5]),
            goal="Mixed",
        )
        out = render_dag_ascii(plan)
        for status in TaskStatus:
            assert STATUS_ICONS[status] in out, f"Missing icon for {status}"
