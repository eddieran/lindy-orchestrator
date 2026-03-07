"""Tests for CLI shared helpers — resolve_goal, load_cfg, persist_plan, finalise_session."""

from __future__ import annotations

import json
from unittest.mock import patch

import click.exceptions
import pytest

from lindy_orchestrator.cli_helpers import (
    finalise_session,
    load_cfg,
    make_on_progress,
    persist_plan,
    plan_from_dict,
    plan_to_dict,
    resolve_goal,
)
from lindy_orchestrator.models import QACheck, TaskItem, TaskPlan, TaskStatus
from lindy_orchestrator.session import SessionManager


class TestMakeOnProgress:
    def test_returns_callable(self):
        from rich.console import Console
        from io import StringIO

        con = Console(file=StringIO())
        cb = make_on_progress(con)
        assert callable(cb)

    def test_callback_prints(self):
        from rich.console import Console
        from io import StringIO

        buf = StringIO()
        con = Console(file=buf)
        cb = make_on_progress(con)
        cb("hello")
        buf.seek(0)
        assert "hello" in buf.read()


class TestResolveGoal:
    def test_from_argument(self):
        assert resolve_goal("Build auth module", None) == "Build auth module"

    def test_from_file(self, tmp_path):
        f = tmp_path / "goal.txt"
        f.write_text("  Deploy to production  ")
        assert resolve_goal(None, str(f)) == "Deploy to production"

    def test_file_not_found_exits(self, tmp_path):
        with pytest.raises(click.exceptions.Exit):
            resolve_goal(None, str(tmp_path / "nope.txt"))

    def test_no_goal_no_file_exits(self):
        with pytest.raises(click.exceptions.Exit):
            resolve_goal(None, None)

    def test_stdin_dash(self):
        with patch("sys.stdin") as mock_stdin:
            mock_stdin.read.return_value = "Goal from stdin\n"
            result = resolve_goal(None, "-")
            assert result == "Goal from stdin"

    def test_stdin_empty_exits(self):
        with patch("sys.stdin") as mock_stdin:
            mock_stdin.read.return_value = "  "
            with pytest.raises(click.exceptions.Exit):
                resolve_goal(None, "-")

    def test_goal_takes_precedence_over_none_file(self):
        result = resolve_goal("my goal", None)
        assert result == "my goal"


class TestLoadCfg:
    def test_valid_config(self, tmp_path):
        cfg_file = tmp_path / "orchestrator.yaml"
        cfg_file.write_text("project:\n  name: test\nmodules:\n  - name: mod1\n    path: mod1/\n")
        cfg = load_cfg(str(cfg_file))
        assert cfg.project.name == "test"

    def test_file_not_found_exits(self, tmp_path):
        with pytest.raises(click.exceptions.Exit):
            load_cfg(str(tmp_path / "nonexistent.yaml"))

    def test_invalid_yaml_exits(self, tmp_path):
        cfg_file = tmp_path / "orchestrator.yaml"
        cfg_file.write_text("{{invalid yaml")
        with pytest.raises(click.exceptions.Exit):
            load_cfg(str(cfg_file))


class TestPlanToFromDict:
    def test_roundtrip(self):
        plan = TaskPlan(
            goal="Test goal",
            tasks=[
                TaskItem(
                    id=1,
                    module="backend",
                    description="Do thing",
                    qa_checks=[QACheck(gate="ci_check", params={"repo": "org/repo"})],
                )
            ],
        )
        d = plan_to_dict(plan)
        restored = plan_from_dict(d)
        assert restored.goal == "Test goal"
        assert len(restored.tasks) == 1
        assert restored.tasks[0].module == "backend"


class TestPersistPlan:
    def test_creates_json_and_md(self, tmp_path):
        plan = TaskPlan(
            goal="Deploy v2",
            tasks=[
                TaskItem(id=1, module="backend", description="Update API", prompt="Do it"),
                TaskItem(
                    id=2,
                    module="frontend",
                    description="Update UI",
                    depends_on=[1],
                    qa_checks=[QACheck(gate="ci_check")],
                ),
            ],
        )
        json_path = persist_plan(tmp_path, plan)
        assert json_path.exists()
        assert json_path.suffix == ".json"

        # Check JSON is valid
        data = json.loads(json_path.read_text())
        assert data["goal"] == "Deploy v2"
        assert len(data["tasks"]) == 2

        # Check latest.md was created
        md_path = tmp_path / ".orchestrator" / "plans" / "latest.md"
        assert md_path.exists()
        md_content = md_path.read_text()
        assert "Deploy v2" in md_content
        assert "Task 1" in md_content
        assert "Task 2" in md_content

    def test_empty_goal_slug(self, tmp_path):
        plan = TaskPlan(goal="", tasks=[])
        json_path = persist_plan(tmp_path, plan)
        assert json_path.exists()

    def test_special_chars_in_goal(self, tmp_path):
        plan = TaskPlan(goal="Fix bug #123 & deploy!", tasks=[])
        json_path = persist_plan(tmp_path, plan)
        assert json_path.exists()
        # Filename should be sanitized
        assert "#" not in json_path.name
        assert "&" not in json_path.name


class TestFinaliseSession:
    def _make_plan(self, statuses):
        tasks = []
        for i, s in enumerate(statuses, 1):
            tasks.append(TaskItem(id=i, module=f"mod{i}", description=f"Task {i}", status=s))
        return TaskPlan(goal="Goal", tasks=tasks)

    def test_all_completed(self, tmp_path):
        sessions = SessionManager(tmp_path / "sessions")
        session = sessions.create(goal="Goal")
        plan = self._make_plan([TaskStatus.COMPLETED, TaskStatus.COMPLETED])

        completed, failed = finalise_session(session, sessions, plan)
        assert len(completed) == 2
        assert len(failed) == 0
        # Session should be marked completed
        loaded = sessions.load(session.session_id)
        assert loaded.status == "completed"

    def test_with_failures(self, tmp_path):
        sessions = SessionManager(tmp_path / "sessions")
        session = sessions.create(goal="Goal")
        plan = self._make_plan([TaskStatus.COMPLETED, TaskStatus.FAILED])

        completed, failed = finalise_session(session, sessions, plan)
        assert len(completed) == 1
        assert len(failed) == 1
        loaded = sessions.load(session.session_id)
        assert loaded.status == "paused"

    def test_plan_json_saved(self, tmp_path):
        sessions = SessionManager(tmp_path / "sessions")
        session = sessions.create(goal="Goal")
        plan = self._make_plan([TaskStatus.COMPLETED])

        finalise_session(session, sessions, plan)
        loaded = sessions.load(session.session_id)
        assert loaded.plan_json is not None
        assert loaded.plan_json["goal"] == "Goal"

    def test_completed_tasks_metadata(self, tmp_path):
        sessions = SessionManager(tmp_path / "sessions")
        session = sessions.create(goal="Goal")
        plan = self._make_plan([TaskStatus.COMPLETED])

        finalise_session(session, sessions, plan)
        loaded = sessions.load(session.session_id)
        assert len(loaded.completed_tasks) == 1
        assert loaded.completed_tasks[0]["id"] == 1
        assert loaded.completed_tasks[0]["module"] == "mod1"
