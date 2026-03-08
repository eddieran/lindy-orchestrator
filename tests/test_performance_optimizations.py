"""Tests for performance optimizations: parallel QA, CI backoff, stall thresholds."""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import patch

from lindy_orchestrator.config import (
    DispatcherConfig,
    OrchestratorConfig,
    StallEscalationConfig,
)
from lindy_orchestrator.models import QACheck
from lindy_orchestrator.prompts import render_plan_prompt
from lindy_orchestrator.scheduler_helpers import _autofill_ci_params


# ---------------------------------------------------------------------------
# P0: Stall threshold defaults
# ---------------------------------------------------------------------------


class TestStallThresholdDefaults:
    def test_reduced_defaults(self):
        cfg = StallEscalationConfig()
        assert cfg.warn_after_seconds == 150
        assert cfg.kill_after_seconds == 600

    def test_custom_override_respected(self):
        cfg = StallEscalationConfig(warn_after_seconds=60, kill_after_seconds=120)
        assert cfg.warn_after_seconds == 60
        assert cfg.kill_after_seconds == 120


class TestStallGracePeriodNoFloor:
    """Verify stall detection no longer has artificial 300/600s minimums."""

    @patch("lindy_orchestrator.dispatcher.subprocess.Popen")
    @patch("lindy_orchestrator.dispatcher.find_claude_cli", return_value="/usr/bin/claude")
    def test_low_stall_threshold_honored(self, mock_cli, mock_popen, tmp_path):
        """A stall_seconds=3 should kill at ~6s (first event grace = 2x), not 600s."""
        from tests.test_dispatcher import FakeStallingPopen
        from lindy_orchestrator.dispatcher import dispatch_agent

        fake = FakeStallingPopen()
        mock_popen.return_value = fake

        config = DispatcherConfig(
            timeout_seconds=30,
            stall_timeout_seconds=10,
        )

        start = time.monotonic()
        result = dispatch_agent(
            "backend",
            tmp_path,
            "test",
            config,
            stall_seconds=3,
        )
        elapsed = time.monotonic() - start

        assert result.success is False
        assert result.error in ("stall", "timeout")
        # Should finish much faster than the old 600s floor
        assert elapsed < 20


# ---------------------------------------------------------------------------
# P0: CI check exponential backoff
# ---------------------------------------------------------------------------


class TestCICheckBackoff:
    """Verify exponential backoff in CI polling."""

    def test_backoff_timing(self):
        """First poll at 5s, then 10s, 20s, capped at poll_interval (30s)."""
        from lindy_orchestrator.qa.ci_check import CICheckGate

        gate = CICheckGate()
        poll_times: list[float] = []

        def mock_sleep(seconds):
            poll_times.append(seconds)
            # Don't actually sleep in tests
            raise StopIteration("stop after recording")

        with patch("lindy_orchestrator.qa.ci_check.time.sleep", side_effect=mock_sleep):
            with patch.object(gate, "_query_runs", return_value=None):
                try:
                    gate.check(
                        params={"repo": "org/repo", "branch": "test", "poll_interval": 30},
                        project_root=Path("/tmp"),
                    )
                except StopIteration:
                    pass

        # First sleep should be 5s (min of 5, poll_interval)
        assert poll_times[0] == 5

    def test_backoff_sequence(self):
        """Verify the full backoff sequence: 5, 10, 20, 30, 30..."""
        from lindy_orchestrator.qa.ci_check import CICheckGate

        gate = CICheckGate()
        poll_times: list[float] = []
        call_count = 0

        def mock_sleep(seconds):
            nonlocal call_count
            poll_times.append(seconds)
            call_count += 1
            if call_count >= 5:
                raise StopIteration("enough data")

        with patch("lindy_orchestrator.qa.ci_check.time.sleep", side_effect=mock_sleep):
            with patch.object(gate, "_query_runs", return_value=None):
                try:
                    gate.check(
                        params={"repo": "org/repo", "branch": "test", "poll_interval": 30},
                        project_root=Path("/tmp"),
                    )
                except StopIteration:
                    pass

        assert poll_times == [5, 10, 20, 30, 30]

    def test_small_poll_interval_caps_correctly(self):
        """When poll_interval is 10, backoff: 5, 10, 10, 10..."""
        from lindy_orchestrator.qa.ci_check import CICheckGate

        gate = CICheckGate()
        poll_times: list[float] = []
        call_count = 0

        def mock_sleep(seconds):
            nonlocal call_count
            poll_times.append(seconds)
            call_count += 1
            if call_count >= 4:
                raise StopIteration()

        with patch("lindy_orchestrator.qa.ci_check.time.sleep", side_effect=mock_sleep):
            with patch.object(gate, "_query_runs", return_value=None):
                try:
                    gate.check(
                        params={"repo": "org/repo", "branch": "test", "poll_interval": 10},
                        project_root=Path("/tmp"),
                    )
                except StopIteration:
                    pass

        assert poll_times == [5, 10, 10, 10]


# ---------------------------------------------------------------------------
# P0: Parallel QA gates
# ---------------------------------------------------------------------------


class TestAutofillCIParams:
    def test_fills_branch_and_repo(self):
        checks = [QACheck(gate="ci_check", params={})]
        config = OrchestratorConfig()
        config.modules = []
        _autofill_ci_params(checks, "af/task-1", config, "root")
        assert checks[0].params["branch"] == "af/task-1"

    def test_skips_non_ci_gates(self):
        checks = [QACheck(gate="structural_check", params={})]
        config = OrchestratorConfig()
        _autofill_ci_params(checks, "af/task-1", config, "root")
        assert "branch" not in checks[0].params

    def test_preserves_existing_params(self):
        checks = [QACheck(gate="ci_check", params={"branch": "custom", "repo": "org/repo"})]
        config = OrchestratorConfig()
        _autofill_ci_params(checks, "af/task-1", config, "root")
        assert checks[0].params["branch"] == "custom"
        assert checks[0].params["repo"] == "org/repo"


# ---------------------------------------------------------------------------
# P1: Architecture truncation
# ---------------------------------------------------------------------------


class TestArchitectureTruncation:
    def test_short_architecture_not_truncated(self):
        arch = "# Architecture\nSimple project."
        result = render_plan_prompt(
            goal="Goal",
            module_summaries={"m": "ok"},
            architecture=arch,
        )
        assert "Simple project." in result
        assert "truncated" not in result

    def test_long_architecture_truncated(self):
        arch = "A" * 6000
        result = render_plan_prompt(
            goal="Goal",
            module_summaries={"m": "ok"},
            architecture=arch,
        )
        assert "truncated" in result
        assert "ARCHITECTURE.md" in result
        # Should contain the first 5000 chars
        assert "A" * 5000 in result
        # Should not contain the full 6000 chars
        assert "A" * 6000 not in result

    def test_exact_boundary_not_truncated(self):
        arch = "B" * 5000
        result = render_plan_prompt(
            goal="Goal",
            module_summaries={"m": "ok"},
            architecture=arch,
        )
        assert "truncated" not in result
        assert "B" * 5000 in result

    def test_no_architecture(self):
        result = render_plan_prompt(
            goal="Goal",
            module_summaries={"m": "ok"},
            architecture=None,
        )
        assert "Architecture" not in result
