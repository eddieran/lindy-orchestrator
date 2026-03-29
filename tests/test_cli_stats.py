"""Tests for the CLI stats command."""

from __future__ import annotations

import json
from pathlib import Path

import yaml
from typer.testing import CliRunner

from lindy_orchestrator.cli import app
from lindy_orchestrator.session import session_file_path


runner = CliRunner()


def _setup_project(tmp_path: Path, sessions: list[dict] | None = None) -> str:
    """Create a minimal orchestrator project and return config path."""
    orch_dir = tmp_path / ".orchestrator"
    orch_dir.mkdir(parents=True, exist_ok=True)

    config = {
        "project": {"name": "test-project"},
        "modules": [{"name": "backend", "path": "backend/"}],
    }
    config_path = orch_dir / "config.yaml"
    config_path.write_text(yaml.dump(config), encoding="utf-8")

    # Create required dirs
    (orch_dir / "sessions").mkdir(exist_ok=True)
    (orch_dir / "logs").mkdir(exist_ok=True)
    (orch_dir / "status").mkdir(exist_ok=True)
    (tmp_path / "backend").mkdir(exist_ok=True)

    # Write session files
    if sessions:
        for s in sessions:
            sid = s.get("session_id", "test")
            (orch_dir / "sessions" / f"{sid}.json").write_text(json.dumps(s), encoding="utf-8")

    return str(config_path)


def _make_session(
    session_id: str = "abc123",
    goal: str = "Test goal",
    status: str = "completed",
    tasks: list[dict] | None = None,
) -> dict:
    if tasks is None:
        tasks = [
            {
                "id": 1,
                "module": "backend",
                "description": "T1",
                "status": "completed",
                "cost_usd": 0.05,
            },
        ]
    return {
        "session_id": session_id,
        "goal": goal,
        "status": status,
        "started_at": "2026-01-01T00:00:00+00:00",
        "completed_at": "2026-01-01T00:05:00+00:00",
        "plan_json": {"goal": goal, "tasks": tasks},
    }


class TestStatsNoSessions:
    def test_no_sessions_shows_message(self, tmp_path: Path):
        cfg_path = _setup_project(tmp_path)
        result = runner.invoke(app, ["stats", "-c", cfg_path])
        assert result.exit_code == 0
        assert "No sessions found" in result.output

    def test_no_sessions_json(self, tmp_path: Path):
        cfg_path = _setup_project(tmp_path)
        result = runner.invoke(app, ["stats", "-c", cfg_path, "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["sessions"] == []
        assert "No sessions found" in data.get("message", "")


class TestStatsWithSessions:
    def test_sessions_show_table(self, tmp_path: Path):
        session = _make_session()
        cfg_path = _setup_project(tmp_path, sessions=[session])
        result = runner.invoke(app, ["stats", "-c", cfg_path])
        assert result.exit_code == 0
        assert "Aggregate Stats" in result.output
        assert "abc123" in result.output

    def test_json_outputs_valid_json(self, tmp_path: Path):
        session = _make_session()
        cfg_path = _setup_project(tmp_path, sessions=[session])
        result = runner.invoke(app, ["stats", "-c", cfg_path, "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "total_cost" in data
        assert "sessions" in data
        assert len(data["sessions"]) == 1

    def test_cost_only_shows_cost_table(self, tmp_path: Path):
        session = _make_session()
        cfg_path = _setup_project(tmp_path, sessions=[session])
        result = runner.invoke(app, ["stats", "-c", cfg_path, "--cost-only"])
        assert result.exit_code == 0
        assert "Total Cost" in result.output
        # Should NOT show full aggregate stats header
        assert "Aggregate Stats" not in result.output

    def test_module_filter(self, tmp_path: Path):
        sessions = [
            _make_session(
                session_id="s1",
                tasks=[
                    {
                        "id": 1,
                        "module": "backend",
                        "description": "T1",
                        "status": "completed",
                        "cost_usd": 0.05,
                    },
                ],
            ),
            _make_session(
                session_id="s2",
                tasks=[
                    {
                        "id": 1,
                        "module": "frontend",
                        "description": "T1",
                        "status": "completed",
                        "cost_usd": 0.10,
                    },
                ],
            ),
        ]
        cfg_path = _setup_project(tmp_path, sessions=sessions)
        result = runner.invoke(app, ["stats", "-c", cfg_path, "--module", "backend"])
        assert result.exit_code == 0
        # Should show backend, not frontend in module breakdown
        assert "backend" in result.output

    def test_limit_flag(self, tmp_path: Path):
        sessions = [_make_session(session_id=f"s{i}", goal=f"Goal {i}") for i in range(5)]
        cfg_path = _setup_project(tmp_path, sessions=sessions)
        result = runner.invoke(app, ["stats", "-c", cfg_path, "-n", "2", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert len(data["sessions"]) == 2

    def test_directory_layout_sessions_are_loaded(self, tmp_path: Path):
        session = _make_session()
        cfg_path = _setup_project(tmp_path)
        session_path = session_file_path(
            tmp_path / ".orchestrator" / "sessions", session["session_id"]
        )
        session_path.parent.mkdir(parents=True, exist_ok=True)
        session_path.write_text(json.dumps(session), encoding="utf-8")

        result = runner.invoke(app, ["stats", "-c", cfg_path, "--json"])

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert [item["session_id"] for item in data["sessions"]] == ["abc123"]


class TestStatsEdgeCases:
    def test_json_with_multiple_modules(self, tmp_path: Path):
        """JSON output should include per_module breakdown."""
        sessions = [
            _make_session(
                session_id="s1",
                tasks=[
                    {"id": 1, "module": "backend", "status": "completed", "cost_usd": 0.10},
                    {"id": 2, "module": "frontend", "status": "failed", "cost_usd": 0.05},
                ],
            ),
        ]
        cfg_path = _setup_project(tmp_path, sessions=sessions)
        result = runner.invoke(app, ["stats", "-c", cfg_path, "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "per_module" in data
        assert "backend" in data["per_module"]
        assert "frontend" in data["per_module"]

    def test_cost_only_with_no_modules(self, tmp_path: Path):
        """cost-only with tasks missing module should still work."""
        sessions = [
            _make_session(
                session_id="s1",
                tasks=[{"id": 1, "status": "completed", "cost_usd": 0.01}],
            ),
        ]
        cfg_path = _setup_project(tmp_path, sessions=sessions)
        result = runner.invoke(app, ["stats", "-c", cfg_path, "--cost-only"])
        assert result.exit_code == 0
        assert "Total Cost" in result.output

    def test_stats_with_all_failed_tasks(self, tmp_path: Path):
        sessions = [
            _make_session(
                session_id="s1",
                status="failed",
                tasks=[
                    {"id": 1, "module": "backend", "status": "failed", "cost_usd": 0.01},
                    {"id": 2, "module": "backend", "status": "failed", "cost_usd": 0.02},
                ],
            ),
        ]
        cfg_path = _setup_project(tmp_path, sessions=sessions)
        result = runner.invoke(app, ["stats", "-c", cfg_path])
        assert result.exit_code == 0
        assert "Aggregate Stats" in result.output

    def test_stats_json_output_has_all_fields(self, tmp_path: Path):
        session = _make_session()
        cfg_path = _setup_project(tmp_path, sessions=[session])
        result = runner.invoke(app, ["stats", "-c", cfg_path, "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        for field in (
            "total_cost",
            "total_tasks",
            "completed",
            "failed",
            "skipped",
            "qa_pass_rate",
            "avg_duration",
            "failure_rate",
            "per_module",
            "sessions",
        ):
            assert field in data


class TestStatsHelp:
    def test_help_flag(self):
        result = runner.invoke(app, ["stats", "--help"])
        assert result.exit_code == 0
        assert (
            "cross-session" in result.output.lower()
            or "analytics" in result.output.lower()
            or "cost" in result.output.lower()
        )
