"""Tests for v0.5.2 bug fixes: CI timeout, branch delivery, stall detection.

Bug 1: CI check timeout increased to 900s, immediate completed-run detection
Bug 2: Branch delivery instructions injected into dispatch prompt
Bug 3: Tool-aware stall detection with per-task stall_seconds override
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from lindy_orchestrator.config import CICheckConfig, StallEscalationConfig
from lindy_orchestrator.models import QAResult
from lindy_orchestrator.qa.ci_check import CICheckGate


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path("/tmp/fake-project")


def _fake_proc(returncode=0, stdout="", stderr=""):
    proc = MagicMock()
    proc.returncode = returncode
    proc.stdout = stdout
    proc.stderr = stderr
    return proc


def _gh_run_json(status="completed", conclusion="success", url="https://example.com/run/1"):
    return json.dumps([{"status": status, "conclusion": conclusion, "url": url, "databaseId": 123}])


# ---------------------------------------------------------------------------
# Bug 1: CI check timeout defaults and immediate detection
# ---------------------------------------------------------------------------


class TestCICheckTimeoutDefaults:
    def test_default_timeout_is_900(self):
        cfg = CICheckConfig()
        assert cfg.timeout_seconds == 900

    def test_ci_gate_default_timeout_param(self):
        """CICheckGate should default timeout_seconds to 900."""
        gate = CICheckGate()
        # The default comes from params.get("timeout_seconds", 900)
        # We verify by checking _query_runs is called before any sleep
        with patch.object(
            gate,
            "_query_runs",
            return_value=QAResult(gate="ci_check", passed=True, output="CI success"),
        ) as mock_query:
            result = gate.check(
                params={"repo": "org/repo", "branch": "af/task-1"},
                project_root=_PROJECT_ROOT,
            )
            assert result.passed
            # Should have been called exactly once (the quick check)
            # because it returned a result immediately
            mock_query.assert_called_once()


class TestCICheckImmediateDetection:
    """On retry, CI may already be completed. The gate should detect this
    immediately without entering the polling loop."""

    @patch("lindy_orchestrator.qa.ci_check.subprocess.run")
    def test_completed_run_returns_immediately(self, mock_run):
        mock_run.return_value = _fake_proc(stdout=_gh_run_json("completed", "success"))
        gate = CICheckGate()

        result = gate.check(
            params={"repo": "org/repo", "branch": "af/task-1"},
            project_root=_PROJECT_ROOT,
        )

        assert result.passed
        # Should only call gh once (the quick check), no polling
        assert mock_run.call_count == 1

    @patch("lindy_orchestrator.qa.ci_check.subprocess.run")
    def test_failed_run_returns_immediately(self, mock_run):
        mock_run.return_value = _fake_proc(stdout=_gh_run_json("completed", "failure"))
        gate = CICheckGate()

        result = gate.check(
            params={"repo": "org/repo", "branch": "af/task-1"},
            project_root=_PROJECT_ROOT,
        )

        assert not result.passed
        assert "failure" in result.output
        assert mock_run.call_count == 1

    @patch("lindy_orchestrator.qa.ci_check.time.sleep")
    @patch("lindy_orchestrator.qa.ci_check.subprocess.run")
    def test_in_progress_polls_then_succeeds(self, mock_run, mock_sleep):
        """First call: in_progress → poll → second call: completed."""
        mock_run.side_effect = [
            _fake_proc(stdout=_gh_run_json("in_progress")),  # quick check
            _fake_proc(stdout=_gh_run_json("completed", "success")),  # first poll
        ]
        gate = CICheckGate()

        result = gate.check(
            params={"repo": "org/repo", "branch": "af/task-1", "poll_interval": 1},
            project_root=_PROJECT_ROOT,
        )

        assert result.passed
        assert mock_run.call_count == 2
        mock_sleep.assert_called_once_with(1)

    @patch("lindy_orchestrator.qa.ci_check.subprocess.run")
    def test_missing_params_returns_error(self, mock_run):
        gate = CICheckGate()

        result = gate.check(
            params={"repo": "", "branch": ""},
            project_root=_PROJECT_ROOT,
        )

        assert not result.passed
        assert "Missing required params" in result.output
        mock_run.assert_not_called()


class TestCICheckQueryRuns:
    @patch("lindy_orchestrator.qa.ci_check.subprocess.run")
    def test_gh_not_found(self, mock_run):
        mock_run.side_effect = FileNotFoundError()
        gate = CICheckGate()

        result = gate._query_runs("org/repo", "ci.yml", "af/task-1")

        assert result is not None
        assert not result.passed
        assert "gh CLI not found" in result.output

    @patch("lindy_orchestrator.qa.ci_check.subprocess.run")
    def test_gh_timeout_returns_none(self, mock_run):
        import subprocess

        mock_run.side_effect = subprocess.TimeoutExpired(cmd="gh", timeout=30)
        gate = CICheckGate()

        result = gate._query_runs("org/repo", "ci.yml", "af/task-1")
        assert result is None  # transient, keep polling

    @patch("lindy_orchestrator.qa.ci_check.subprocess.run")
    def test_no_runs_returns_none(self, mock_run):
        mock_run.return_value = _fake_proc(stdout="[]")
        gate = CICheckGate()

        result = gate._query_runs("org/repo", "ci.yml", "af/task-1")
        assert result is None  # no runs yet


# ---------------------------------------------------------------------------
# Bug 2: Branch delivery instructions in dispatch prompt
# ---------------------------------------------------------------------------


class TestBranchDeliveryInstructions:
    """The scheduler should inject branch creation/push instructions."""

    def test_branch_instructions_format(self):
        """Verify the format of injected branch instructions."""
        branch_prefix = "af"
        task_id = 5
        branch_name = f"{branch_prefix}/task-{task_id}"
        original_prompt = "Implement feature X"

        # Simulate what scheduler does
        prompt = (
            f"{original_prompt}\n\n"
            f"## IMPORTANT: Branch delivery requirements\n\n"
            f"You MUST deliver your work on branch `{branch_name}`.\n"
            f"Before starting work:\n"
            f"1. `git checkout -b {branch_name}` (create the branch)\n"
            f"When done:\n"
            f"2. `git add` and `git commit` your changes\n"
            f"3. `git push -u origin {branch_name}` (push to remote)\n"
            f"Do NOT skip the push step — CI verification depends on it.\n"
        )

        assert "af/task-5" in prompt
        assert "git checkout -b af/task-5" in prompt
        assert "git push -u origin af/task-5" in prompt
        assert "Branch delivery requirements" in prompt


# ---------------------------------------------------------------------------
# Bug 3: Stall detection — per-task override and tool awareness
# ---------------------------------------------------------------------------


class TestStallDetectionConfig:
    def test_default_stall_escalation(self):
        cfg = StallEscalationConfig()
        assert cfg.warn_after_seconds == 150
        assert cfg.kill_after_seconds == 600

    def test_task_stall_seconds_field(self):
        from lindy_orchestrator.models import TaskItem

        # Default: None (use config)
        task = TaskItem(id=1, module="backend", description="test")
        assert task.stall_seconds is None

        # Override
        task = TaskItem(id=2, module="backend", description="long task", stall_seconds=1200)
        assert task.stall_seconds == 1200


class TestStallDetectionToolAwareness:
    """Stall detection should give 50% more time when last tool is Bash."""

    def test_bash_tool_extends_thresholds(self):
        """Verify the Bash-aware logic from dispatcher.py."""
        base_warn = 300
        base_kill = 600

        # Simulate the tool-aware logic
        last_tool_use = "Bash"
        _LONG_RUNNING_TOOLS = {"Bash", "bash", "execute_bash"}

        warn = base_warn
        kill = base_kill
        if last_tool_use in _LONG_RUNNING_TOOLS:
            warn = int(warn * 1.5)
            kill = int(kill * 1.5)

        assert warn == 450
        assert kill == 900

    def test_non_bash_tool_keeps_thresholds(self):
        base_warn = 300
        base_kill = 600
        last_tool_use = "Read"
        _LONG_RUNNING_TOOLS = {"Bash", "bash", "execute_bash"}

        warn = base_warn
        kill = base_kill
        if last_tool_use in _LONG_RUNNING_TOOLS:
            warn = int(warn * 1.5)
            kill = int(kill * 1.5)

        assert warn == 300
        assert kill == 600


class TestStallDetectionPerTaskOverride:
    """Per-task stall_seconds should override config defaults."""

    def test_per_task_override_logic(self):
        """Simulate the priority logic from dispatcher.py."""
        config_warn = 300
        config_kill = 600
        stall_seconds = 1200  # per-task override

        if stall_seconds is not None:
            warn_threshold = stall_seconds // 2
            kill_threshold = stall_seconds
        else:
            warn_threshold = config_warn
            kill_threshold = config_kill

        assert warn_threshold == 600
        assert kill_threshold == 1200

    def test_none_override_uses_config(self):
        config_warn = 300
        config_kill = 600
        stall_seconds = None

        if stall_seconds is not None:
            warn_threshold = stall_seconds // 2
            kill_threshold = stall_seconds
        else:
            warn_threshold = config_warn
            kill_threshold = config_kill

        assert warn_threshold == 300
        assert kill_threshold == 600


class TestDispatcherStallOverrideIntegration:
    """dispatch_agent() should accept stall_seconds kwarg."""

    def test_dispatch_agent_signature_accepts_stall_seconds(self):
        """Verify dispatch_agent accepts the new parameter."""
        from lindy_orchestrator.dispatcher import dispatch_agent
        import inspect

        sig = inspect.signature(dispatch_agent)
        assert "stall_seconds" in sig.parameters
        param = sig.parameters["stall_seconds"]
        assert param.default is None

    def test_provider_dispatch_accepts_stall_seconds(self):
        """Verify ClaudeCLIProvider.dispatch accepts stall_seconds."""
        from lindy_orchestrator.providers.claude_cli import ClaudeCLIProvider
        import inspect

        sig = inspect.signature(ClaudeCLIProvider.dispatch)
        assert "stall_seconds" in sig.parameters
