"""Tests for mid-execution checkpointing and resume."""

from __future__ import annotations

import json

from lindy_orchestrator.models import (
    QACheck,
    TaskSpec,
    TaskPlan,
    TaskStatus,
    plan_from_dict,
    plan_to_dict,
)
from lindy_orchestrator.session import SessionManager


class TestPlanSerialization:
    def test_plan_to_dict_roundtrip(self):
        plan = TaskPlan(
            goal="Add auth",
            tasks=[
                TaskSpec(id=1, module="backend", description="Add JWT"),
                TaskSpec(
                    id=2,
                    module="frontend",
                    description="Login page",
                    depends_on=[1],
                    qa_checks=[QACheck(gate="structural_check", params={"max_file_lines": 500})],
                ),
            ],
        )
        data = plan_to_dict(plan)
        restored = plan_from_dict(data)

        assert restored.goal == "Add auth"
        assert len(restored.tasks) == 2
        assert restored.tasks[0].module == "backend"
        assert restored.tasks[1].depends_on == [1]
        assert restored.tasks[1].qa_checks[0].gate == "structural_check"

    def test_plan_to_dict_preserves_status(self):
        plan = TaskPlan(
            goal="Test",
            tasks=[
                TaskSpec(id=1, module="m", description="d", status=TaskStatus.COMPLETED),
                TaskSpec(id=2, module="m", description="d", status=TaskStatus.FAILED),
            ],
        )
        data = plan_to_dict(plan)
        restored = plan_from_dict(data)

        assert restored.tasks[0].status == TaskStatus.COMPLETED
        assert restored.tasks[1].status == TaskStatus.FAILED

    def test_plan_to_dict_is_json_safe(self):
        plan = TaskPlan(
            goal="Test",
            tasks=[TaskSpec(id=1, module="m", description="d")],
        )
        data = plan_to_dict(plan)
        # Should not raise
        json_str = json.dumps(data, default=str)
        assert "Test" in json_str

    def test_plan_from_dict_handles_missing_fields(self):
        data = {
            "goal": "Minimal",
            "tasks": [
                {"id": 1, "module": "m", "description": "d"},
            ],
        }
        plan = plan_from_dict(data)
        assert plan.tasks[0].prompt == ""
        assert plan.tasks[0].depends_on == []
        assert plan.tasks[0].status == TaskStatus.PENDING


class TestCheckpoint:
    def test_checkpoint_saves_plan_state(self, tmp_path):
        mgr = SessionManager(tmp_path)
        session = mgr.create(goal="Test goal")
        assert session.checkpoint_count == 0

        plan = TaskPlan(
            goal="Test goal",
            tasks=[
                TaskSpec(id=1, module="m", description="d", status=TaskStatus.COMPLETED),
                TaskSpec(id=2, module="m", description="d2", status=TaskStatus.PENDING),
            ],
        )
        mgr.checkpoint(session, plan_to_dict(plan))

        assert session.checkpoint_count == 1
        assert session.last_checkpoint_at is not None

    def test_checkpoint_increments_count(self, tmp_path):
        mgr = SessionManager(tmp_path)
        session = mgr.create(goal="Test")
        plan_dict = plan_to_dict(
            TaskPlan(goal="Test", tasks=[TaskSpec(id=1, module="m", description="d")])
        )

        mgr.checkpoint(session, plan_dict)
        mgr.checkpoint(session, plan_dict)
        mgr.checkpoint(session, plan_dict)

        assert session.checkpoint_count == 3

    def test_checkpoint_persists_to_disk(self, tmp_path):
        mgr = SessionManager(tmp_path)
        session = mgr.create(goal="Test")

        plan = TaskPlan(
            goal="Test",
            tasks=[
                TaskSpec(id=1, module="be", description="API", status=TaskStatus.COMPLETED),
                TaskSpec(id=2, module="fe", description="UI", status=TaskStatus.PENDING),
            ],
        )
        mgr.checkpoint(session, plan_to_dict(plan))

        # Reload from disk
        loaded = mgr.load(session.session_id)
        assert loaded is not None
        assert loaded.checkpoint_count == 1
        assert loaded.plan_json is not None
        assert loaded.plan_json["goal"] == "Test"

    def test_resume_from_checkpoint(self, tmp_path):
        mgr = SessionManager(tmp_path)
        session = mgr.create(goal="Multi-task")

        # Simulate: task 1 completes, task 2 and 3 pending
        plan = TaskPlan(
            goal="Multi-task",
            tasks=[
                TaskSpec(id=1, module="a", description="step1", status=TaskStatus.COMPLETED),
                TaskSpec(id=2, module="b", description="step2", depends_on=[1]),
                TaskSpec(id=3, module="c", description="step3", depends_on=[2]),
            ],
        )
        mgr.checkpoint(session, plan_to_dict(plan))

        # Simulate process restart — load latest session
        restored_session = mgr.load_latest()
        assert restored_session is not None

        restored_plan = plan_from_dict(restored_session.plan_json)
        assert restored_plan.goal == "Multi-task"
        assert restored_plan.tasks[0].status == TaskStatus.COMPLETED
        assert restored_plan.tasks[1].status == TaskStatus.PENDING
        assert restored_plan.tasks[2].status == TaskStatus.PENDING

        # Resume: next_ready should return task 2 (task 1 is done)
        ready = restored_plan.next_ready()
        assert len(ready) == 1
        assert ready[0].id == 2

    def test_checkpoint_with_failed_task(self, tmp_path):
        mgr = SessionManager(tmp_path)
        session = mgr.create(goal="Test")

        plan = TaskPlan(
            goal="Test",
            tasks=[
                TaskSpec(id=1, module="a", description="ok", status=TaskStatus.COMPLETED),
                TaskSpec(id=2, module="b", description="fail", status=TaskStatus.FAILED),
                TaskSpec(id=3, module="c", description="skip", depends_on=[2]),
            ],
        )
        mgr.checkpoint(session, plan_to_dict(plan))

        loaded = mgr.load(session.session_id)
        restored = plan_from_dict(loaded.plan_json)
        assert restored.tasks[1].status == TaskStatus.FAILED

    def test_backward_compat_no_checkpoint_fields(self, tmp_path):
        """Old sessions without checkpoint fields should load fine."""
        mgr = SessionManager(tmp_path)

        # Simulate old session JSON without checkpoint_count
        old_data = {
            "session_id": "old123",
            "started_at": "2024-01-01T00:00:00",
            "completed_at": None,
            "goal": "Old goal",
            "status": "in_progress",
            "actions_taken": [],
            "pending_tasks": [],
            "completed_tasks": [],
            "plan_json": None,
        }
        path = tmp_path / "old123.json"
        path.write_text(json.dumps(old_data))

        loaded = mgr.load("old123")
        assert loaded is not None
        assert loaded.checkpoint_count == 0
        assert loaded.last_checkpoint_at is None
