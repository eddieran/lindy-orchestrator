"""Integration tests for the layered observability pipeline."""

from __future__ import annotations

import json
from contextlib import ExitStack, contextmanager
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import yaml
from typer.testing import CliRunner

from lindy_orchestrator.cli import app
from lindy_orchestrator.models import (
    DispatchResult,
    EvalFeedback,
    EvalResult,
    QACheck,
    QAResult,
    TaskPlan,
    TaskSpec,
    TaskStatus,
    plan_to_dict,
)
from lindy_orchestrator.session import SessionManager, iter_session_files

from .conftest import MINIMAL_STATUS_MD

runner = CliRunner()


class _ValidateOnlyProvider:
    def validate(self) -> None:
        return None


class _DispatchProvider(_ValidateOnlyProvider):
    def __init__(self, output: str) -> None:
        self.output = output

    def dispatch(
        self,
        module: str,
        working_dir: Path,
        prompt: str,
        on_event=None,
        stall_seconds: int | None = None,
    ) -> DispatchResult:
        del working_dir, prompt, stall_seconds
        if on_event is not None:
            on_event({"type": "function_call", "name": "shell"})
            on_event({"msg": {"type": "agent_message", "message": f"{module} reasoning"}})
        return DispatchResult(
            module=module,
            success=True,
            output=self.output,
            event_count=2,
            last_tool_use="shell",
        )


def _write_config(tmp_path: Path, *, level: int) -> str:
    orch_dir = tmp_path / ".orchestrator"
    status_dir = orch_dir / "status"
    status_dir.mkdir(parents=True, exist_ok=True)
    (tmp_path / "backend").mkdir(parents=True, exist_ok=True)
    (status_dir / "backend.md").write_text(MINIMAL_STATUS_MD.format(name="backend"))

    config = {
        "project": {"name": "observability-e2e", "branch_prefix": "obs"},
        "modules": [{"name": "backend", "path": "backend/"}],
        "observability": {"level": level},
    }
    cfg_path = orch_dir / "config.yaml"
    cfg_path.write_text(yaml.safe_dump(config), encoding="utf-8")
    return str(cfg_path)


def _write_plan(tmp_path: Path, plan: TaskPlan) -> Path:
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan_to_dict(plan), indent=2), encoding="utf-8")
    return plan_path


def _make_task(
    task_id: int,
    description: str,
    *,
    depends_on: list[int] | None = None,
    skip_qa: bool = False,
    status: TaskStatus = TaskStatus.PENDING,
    result: str = "",
) -> TaskSpec:
    kwargs: dict[str, object] = {
        "id": task_id,
        "module": "backend",
        "description": description,
        "depends_on": depends_on or [],
        "status": status,
        "result": result,
        "skip_qa": skip_qa,
    }
    if not skip_qa:
        kwargs["acceptance_criteria"] = "- pytest passes"
        kwargs["qa_checks"] = [QACheck(gate="pytest")]
    return TaskSpec(**kwargs)


def _latest_session(tmp_path: Path) -> tuple[object, Path]:
    sessions_dir = tmp_path / ".orchestrator" / "sessions"
    latest = iter_session_files(sessions_dir)[0]
    sessions = SessionManager(sessions_dir)
    session = sessions.load(latest.parent.name)
    assert session is not None
    return session, latest.parent


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    entries: list[dict[str, object]] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError as exc:
            raise AssertionError(f"{path} line {line_no} is not valid JSON: {exc}") from exc
        assert isinstance(entry, dict), f"{path} line {line_no} must decode to an object"
        entries.append(entry)
    return entries


def _assert_required_fields(entries: list[dict[str, object]], *, level: int) -> None:
    required = {"ts", "level", "event", "task_id"}
    for entry in entries:
        assert required <= entry.keys()
        assert entry["level"] == level
        if entry["task_id"] is not None:
            assert "module" in entry


@contextmanager
def _patched_pipeline(
    *,
    agent_output: str = "mock agent output",
    eval_result: EvalResult | None = None,
):
    default_eval = eval_result or EvalResult(
        score=97,
        passed=True,
        retryable=True,
        raw_output='{"score": 97}',
        feedback=EvalFeedback(summary="Looks good"),
        criteria_results=[{"criterion": "pytest passes", "passed": True}],
    )

    def _run_qa_gate(
        *,
        check,
        project_root,
        module_name="",
        task_output="",
        custom_gates=None,
        dispatcher_config=None,
        qa_module=None,
        module_path=None,
    ) -> QAResult:
        del project_root, module_name, task_output, custom_gates, dispatcher_config, qa_module
        del module_path
        return QAResult(gate=check.gate, passed=True, output="ok")

    def _run_eval_agent(self, task, gen_output, qa_results, worktree=None) -> EvalResult:
        del self, task, gen_output, worktree
        return replace(default_eval, qa_results=list(qa_results))

    with ExitStack() as stack:
        stack.enter_context(
            patch("lindy_orchestrator.cli.validate_provider", return_value="claude_cli")
        )
        stack.enter_context(
            patch(
                "lindy_orchestrator.orchestrator.create_provider",
                return_value=_ValidateOnlyProvider(),
            )
        )
        stack.enter_context(
            patch(
                "lindy_orchestrator.generator_runner.create_provider",
                side_effect=lambda _cfg: _DispatchProvider(agent_output),
            )
        )
        stack.enter_context(
            patch("lindy_orchestrator.evaluator_runner.run_qa_gate", side_effect=_run_qa_gate)
        )
        stack.enter_context(
            patch(
                "lindy_orchestrator.evaluator_runner.EvaluatorRunner._run_eval_agent",
                new=_run_eval_agent,
            )
        )
        stack.enter_context(
            patch(
                "lindy_orchestrator.orchestrator.create_worktree", side_effect=lambda root, *_: root
            )
        )
        stack.enter_context(
            patch("lindy_orchestrator.orchestrator.remove_worktree", return_value=None)
        )
        stack.enter_context(
            patch("lindy_orchestrator.orchestrator.cleanup_all_worktrees", return_value=None)
        )
        stack.enter_context(
            patch("lindy_orchestrator.orchestrator._check_delivery", return_value=(True, "ok"))
        )
        stack.enter_context(
            patch(
                "lindy_orchestrator.generator_runner._capture_git_diff",
                return_value=("diff --git a/backend.py b/backend.py", "git diff HEAD"),
            )
        )
        yield


def _run_pipeline(
    tmp_path: Path,
    *,
    level: int,
    goal: str,
    tasks: list[TaskSpec],
    agent_output: str = "mock agent output",
    eval_result: EvalResult | None = None,
) -> tuple[str, object, Path]:
    cfg_path = _write_config(tmp_path, level=level)
    plan_path = _write_plan(tmp_path, TaskPlan(goal=goal, tasks=tasks))

    with _patched_pipeline(agent_output=agent_output, eval_result=eval_result):
        result = runner.invoke(app, ["run", "--plan", str(plan_path), "-c", cfg_path])

    assert result.exit_code == 0, result.output
    session, session_dir = _latest_session(tmp_path)
    return cfg_path, session, session_dir


def _compact(text: str) -> str:
    return "".join(text.split())


def test_level_one_pipeline_writes_summary_lifecycle(tmp_path: Path) -> None:
    _cfg_path, _session, session_dir = _run_pipeline(
        tmp_path,
        level=1,
        goal="Observe level one",
        tasks=[_make_task(1, "Implement backend endpoint", skip_qa=True)],
    )

    summary_entries = _read_jsonl(session_dir / "summary.jsonl")
    _assert_required_fields(summary_entries, level=1)

    assert [entry["event"] for entry in summary_entries] == [
        "session_start",
        "task_started",
        "task_completed",
        "session_end",
    ]
    assert not (session_dir / "decisions.jsonl").exists()
    assert not (session_dir / "transcript.jsonl").exists()


def test_level_two_pipeline_writes_decision_events_with_scores(tmp_path: Path) -> None:
    eval_result = EvalResult(
        score=93,
        passed=True,
        retryable=True,
        raw_output='{"score": 93}',
        feedback=EvalFeedback(summary="Evaluator passed"),
        criteria_results=[{"criterion": "pytest passes", "passed": True}],
    )
    _cfg_path, _session, session_dir = _run_pipeline(
        tmp_path,
        level=2,
        goal="Observe level two",
        tasks=[_make_task(1, "Implement backend endpoint")],
        eval_result=eval_result,
    )

    summary_entries = _read_jsonl(session_dir / "summary.jsonl")
    decisions_entries = _read_jsonl(session_dir / "decisions.jsonl")

    _assert_required_fields(summary_entries, level=1)
    _assert_required_fields(decisions_entries, level=2)

    decision_events = {entry["event"] for entry in decisions_entries}
    assert {"eval_scored", "phase_changed"} <= decision_events

    eval_entries = [entry for entry in decisions_entries if entry["event"] == "eval_scored"]
    assert len(eval_entries) == 1
    assert eval_entries[0]["score"] == 93
    assert eval_entries[0]["reasoning"]["summary"] == "Evaluator passed"


def test_level_three_pipeline_writes_transcript_agent_events(tmp_path: Path) -> None:
    _cfg_path, _session, session_dir = _run_pipeline(
        tmp_path,
        level=3,
        goal="Observe level three",
        tasks=[_make_task(1, "Implement backend endpoint")],
        agent_output="agent output",
    )

    summary_entries = _read_jsonl(session_dir / "summary.jsonl")
    decisions_entries = _read_jsonl(session_dir / "decisions.jsonl")
    transcript_entries = _read_jsonl(session_dir / "transcript.jsonl")

    _assert_required_fields(summary_entries, level=1)
    _assert_required_fields(decisions_entries, level=2)
    _assert_required_fields(transcript_entries, level=3)

    transcript_events = {entry["event"] for entry in transcript_entries}
    assert "agent_event" in transcript_events
    assert "agent_output" in transcript_events
    assert any(entry.get("output") == "agent output" for entry in transcript_entries)


def test_resume_appends_existing_jsonl_streams(tmp_path: Path) -> None:
    cfg_path, session, session_dir = _run_pipeline(
        tmp_path,
        level=3,
        goal="Resume observability",
        tasks=[_make_task(1, "Seed initial observability run")],
        agent_output="first run output",
    )

    summary_before = _read_jsonl(session_dir / "summary.jsonl")
    decisions_before = _read_jsonl(session_dir / "decisions.jsonl")
    transcript_before = _read_jsonl(session_dir / "transcript.jsonl")

    sessions = SessionManager(tmp_path / ".orchestrator" / "sessions")
    loaded = sessions.load(session.session_id)
    assert loaded is not None
    loaded.status = "paused"
    loaded.completed_at = None
    loaded.plan_json = plan_to_dict(
        TaskPlan(
            goal="Resume observability",
            tasks=[
                _make_task(
                    1,
                    "Seed initial observability run",
                    status=TaskStatus.COMPLETED,
                    result="done",
                ),
                _make_task(2, "Resume pending backend work", depends_on=[1]),
            ],
        )
    )
    sessions.save(loaded)

    with _patched_pipeline(agent_output="resume output"):
        result = runner.invoke(app, ["resume", loaded.session_id, "-c", cfg_path])

    assert result.exit_code == 0, result.output

    summary_after = _read_jsonl(session_dir / "summary.jsonl")
    decisions_after = _read_jsonl(session_dir / "decisions.jsonl")
    transcript_after = _read_jsonl(session_dir / "transcript.jsonl")

    assert len(summary_after) > len(summary_before)
    assert len(decisions_after) > len(decisions_before)
    assert len(transcript_after) > len(transcript_before)

    assert summary_after[: len(summary_before)] == summary_before
    assert decisions_after[: len(decisions_before)] == decisions_before
    assert transcript_after[: len(transcript_before)] == transcript_before
    assert any(entry["event"] == "session_resumed" for entry in summary_after)


def test_inspect_reads_pipeline_generated_observability_logs(tmp_path: Path) -> None:
    cfg_path, session, _session_dir = _run_pipeline(
        tmp_path,
        level=3,
        goal="Inspect pipeline output",
        tasks=[_make_task(1, "Inspect backend pipeline")],
        agent_output="inspect output",
    )

    result = runner.invoke(app, ["inspect", session.session_id, "-c", cfg_path, "--full"])
    compact = _compact(result.output)

    assert result.exit_code == 0
    assert "SessionOverview" in compact
    assert "L1Summary" in compact
    assert "L2Decisions" in compact
    assert "L3Transcript" in compact
    assert "Inspectpipelineoutput" in compact
    assert "eval_scored" in result.output
    assert "agent_output" in result.output
    assert "inspectoutput" in compact
