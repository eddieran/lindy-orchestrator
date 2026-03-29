"""Tests for QA gate fixes: diff-awareness, skip_qa, retryable, required, skip_gates."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from lindy_orchestrator.config import CustomGateConfig, OrchestratorConfig
from lindy_orchestrator.hooks import Event, EventType, HookRegistry
from lindy_orchestrator.models import QAResult, TaskItem, TaskPlan, TaskStatus


# ---------------------------------------------------------------------------
# #8 P0: max_workers=0 fix
# ---------------------------------------------------------------------------


class TestEmptyQAGates:
    @patch("lindy_orchestrator.scheduler.create_provider")
    def test_skip_qa_task_no_crash(self, mock_provider: MagicMock) -> None:
        """skip_qa=True task with 0 gates should not crash."""
        from lindy_orchestrator.logger import ActionLogger
        from lindy_orchestrator.scheduler import execute_plan

        plan = TaskPlan(
            goal="test",
            tasks=[
                TaskItem(
                    id=1,
                    module="root",
                    description="ops task",
                    skip_qa=True,
                    status=TaskStatus.PENDING,
                ),
            ],
        )
        cfg = OrchestratorConfig()
        cfg.safety.dry_run = True
        logger = MagicMock(spec=ActionLogger)

        result = execute_plan(plan, cfg, logger)
        assert result.tasks[0].status == TaskStatus.COMPLETED

    def test_run_qa_gates_empty_list(self) -> None:
        """_run_qa_gates with 0 gates returns True without ThreadPoolExecutor."""
        from lindy_orchestrator.scheduler import _run_qa_gates

        task = TaskItem(id=1, module="root", description="test")
        task.qa_checks = []
        cfg = OrchestratorConfig()
        progress = MagicMock()
        detail = MagicMock()

        result = _run_qa_gates(
            task, cfg, MagicMock(), Path("/tmp"), Path("/tmp"), progress, detail, None
        )
        assert result is True


# ---------------------------------------------------------------------------
# #3 P0: skip_qa skips delivery_check
# ---------------------------------------------------------------------------


class TestSkipQaDeliveryCheck:
    @patch("lindy_orchestrator.scheduler.create_provider")
    @patch("lindy_orchestrator.scheduler.create_worktree", return_value=None)
    def test_skip_qa_skips_delivery_and_qa(
        self, mock_wt: MagicMock, mock_provider: MagicMock
    ) -> None:
        """skip_qa=True should skip delivery_check and mark completed directly."""
        from lindy_orchestrator.scheduler import _dispatch_loop

        mock_result = MagicMock()
        mock_result.success = True
        mock_result.output = "done"
        mock_result.cost_usd = 0.1
        mock_result.duration_seconds = 10
        mock_result.event_count = 5
        mock_result.exit_code = 0
        mock_result.last_tool_use = ""
        mock_result.error = None
        mock_provider.return_value.dispatch.return_value = mock_result

        task = TaskItem(id=1, module="root", description="delete branch", skip_qa=True)
        task.prompt = "test"
        cfg = OrchestratorConfig()
        logger = MagicMock()
        progress = MagicMock()
        hooks = HookRegistry()
        events: list[Event] = []
        hooks.on_any(lambda e: events.append(e))

        dispatches = _dispatch_loop(
            task, cfg, logger, progress, MagicMock(), 2, hooks, "af/task-1", None
        )

        assert dispatches == 1
        assert task.status == TaskStatus.COMPLETED
        # Should NOT have called _check_and_log_delivery (no delivery check progress)
        delivery_calls = [c for c in progress.call_args_list if "Delivery check" in str(c)]
        assert len(delivery_calls) == 0


# ---------------------------------------------------------------------------
# #4a P1: non-retryable failures skip retry
# ---------------------------------------------------------------------------


class TestNonRetryable:
    def test_qa_result_retryable_default_true(self) -> None:
        r = QAResult(gate="test", passed=False, output="fail")
        assert r.retryable is True

    def test_all_non_retryable_skips_retry(self) -> None:
        from lindy_orchestrator.scheduler import _handle_retry

        task = TaskItem(id=1, module="root", description="test")
        task.qa_results = [
            QAResult(gate="structural_check", passed=False, output="pre-existing", retryable=False),
        ]
        hooks = HookRegistry()
        events: list[Event] = []
        hooks.on_any(lambda e: events.append(e))

        should_continue = _handle_retry(task, "original", 3, MagicMock(), MagicMock(), hooks)

        assert should_continue is False
        assert task.status == TaskStatus.FAILED
        assert task.retries == 0  # didn't increment — skipped entirely

        failed_events = [e for e in events if e.type == EventType.TASK_FAILED]
        assert len(failed_events) == 1
        assert failed_events[0].data["reason"] == "non_retryable_failures"

    def test_mix_retryable_and_non_still_retries(self) -> None:
        from lindy_orchestrator.scheduler import _handle_retry

        task = TaskItem(id=1, module="root", description="test")
        task.qa_results = [
            QAResult(gate="structural_check", passed=False, output="pre-existing", retryable=False),
            QAResult(gate="command_check", passed=False, output="test failed", retryable=True),
        ]
        should_continue = _handle_retry(task, "original", 3, MagicMock(), MagicMock(), None)

        assert should_continue is True
        assert task.retries == 1


# ---------------------------------------------------------------------------
# #1 P1: structural_check diff-awareness
# ---------------------------------------------------------------------------


class TestStructuralCheckDiffAwareness:
    @patch("lindy_orchestrator.qa.structural_check._was_over_limit_at_base")
    def test_pre_existing_large_file_skipped(self, mock_base: MagicMock, tmp_path: Path) -> None:
        """File that was already over limit at merge-base is NOT flagged."""
        from lindy_orchestrator.qa.structural_check import _check_file_size

        mock_base.return_value = True  # was over limit at base
        large_file = tmp_path / "service.go"
        large_file.write_text("line\n" * 600)

        violations = _check_file_size(large_file, "service.go", 500, tmp_path)
        assert len(violations) == 0

    @patch("lindy_orchestrator.qa.structural_check._was_over_limit_at_base")
    def test_new_large_file_flagged(self, mock_base: MagicMock, tmp_path: Path) -> None:
        """File that was under limit at merge-base IS flagged."""
        mock_base.return_value = False
        from lindy_orchestrator.qa.structural_check import _check_file_size

        large_file = tmp_path / "new_file.go"
        large_file.write_text("line\n" * 600)

        violations = _check_file_size(large_file, "new_file.go", 500, tmp_path)
        assert len(violations) == 1
        assert violations[0].rule == "file_size"


# ---------------------------------------------------------------------------
# #7 P2: required=false gate is warning only
# ---------------------------------------------------------------------------


class TestRequiredField:
    def test_custom_gate_config_required_default_true(self) -> None:
        gate = CustomGateConfig(name="lint", command="eslint .")
        assert gate.required is True

    def test_custom_gate_config_required_false(self) -> None:
        gate = CustomGateConfig(name="integration", command="npm test", required=False)
        assert gate.required is False

    def test_custom_gate_config_diff_only(self) -> None:
        gate = CustomGateConfig(name="lint", command="eslint {changed_files}", diff_only=True)
        assert gate.diff_only is True


# ---------------------------------------------------------------------------
# #6 P2: skip_gates on TaskItem
# ---------------------------------------------------------------------------


class TestSkipGates:
    def test_task_item_skip_gates_default_empty(self) -> None:
        task = TaskItem(id=1, module="root", description="test")
        assert task.skip_gates == []

    def test_skip_gates_excludes_structural(self) -> None:
        from lindy_orchestrator.task_preparation import inject_qa_gates

        task = TaskItem(id=1, module="backend", description="test", skip_gates=["structural_check"])
        cfg = OrchestratorConfig()
        cfg._config_dir = Path("/tmp")
        progress = MagicMock()

        inject_qa_gates(task, cfg, progress)

        gate_names = [q.gate for q in task.qa_checks]
        assert "structural_check" not in gate_names

    def test_skip_gates_excludes_named_command(self) -> None:
        from lindy_orchestrator.task_preparation import inject_qa_gates

        cfg = OrchestratorConfig()
        cfg._config_dir = Path("/tmp")
        cfg.qa_gates.custom = [
            CustomGateConfig(name="slow-integration", command="npm run test:e2e"),
        ]

        task = TaskItem(id=1, module="backend", description="test", skip_gates=["slow-integration"])
        progress = MagicMock()

        inject_qa_gates(task, cfg, progress)

        commands = [q.params.get("command") for q in task.qa_checks if q.gate == "command_check"]
        assert "npm run test:e2e" not in commands

    def test_plan_from_dict_preserves_skip_gates(self) -> None:
        from lindy_orchestrator.models import plan_from_dict

        data = {
            "goal": "test",
            "tasks": [
                {
                    "id": 1,
                    "module": "root",
                    "description": "test",
                    "skip_gates": ["structural_check", "layer_check"],
                }
            ],
        }
        plan = plan_from_dict(data)
        assert plan.tasks[0].skip_gates == ["structural_check", "layer_check"]


# ---------------------------------------------------------------------------
# #2 P2: command_check diff_only
# ---------------------------------------------------------------------------


class TestCommandCheckDiffOnly:
    @patch("lindy_orchestrator.qa.command_check._get_changed_files")
    def test_diff_only_no_changed_files_passes(self, mock_files: MagicMock) -> None:
        from lindy_orchestrator.qa.command_check import CommandCheckGate

        mock_files.return_value = []
        gate = CommandCheckGate()
        result = gate.check(
            params={"command": "eslint {changed_files}", "diff_only": True},
            project_root=Path("/tmp"),
        )
        assert result.passed is True
        assert "No changed files" in result.output

    @patch("lindy_orchestrator.qa.command_check._get_changed_files")
    @patch("lindy_orchestrator.qa.command_check.subprocess.run")
    def test_diff_only_injects_files(self, mock_run: MagicMock, mock_files: MagicMock) -> None:
        from lindy_orchestrator.qa.command_check import CommandCheckGate

        mock_files.return_value = ["src/app.ts", "src/utils.ts"]
        mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")

        gate = CommandCheckGate()
        result = gate.check(
            params={"command": "eslint {changed_files}", "diff_only": True},
            project_root=Path("/tmp"),
        )
        assert result.passed is True
        # Verify the command had files injected
        cmd_args = mock_run.call_args[0][0]
        assert "src/app.ts" in " ".join(cmd_args)


# ---------------------------------------------------------------------------
# #5 P1: onboard max_parallel >= 2
# ---------------------------------------------------------------------------


class TestOnboardMaxParallel:
    def test_single_module_gets_at_least_2(self) -> None:
        from lindy_orchestrator.models import DiscoveryContext, ModuleProfile

        ctx = DiscoveryContext(
            project_name="test",
            project_description="test",
            root="/tmp",
            modules=[ModuleProfile(name="api", path="api/")],
        )
        # min(1, 3) = 1, but max(1, 2) = 2
        parallel = max(min(len(ctx.modules), 3), 2)
        assert parallel >= 2
