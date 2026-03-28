"""Evaluator role runner."""

from __future__ import annotations

import concurrent.futures
import json
import re
from pathlib import Path

from .config import EvaluatorConfig, OrchestratorConfig
from .models import EvalFeedback, EvalResult, GeneratorOutput, QACheck, QAResult, TaskSpec
from .qa import run_qa_gate
from .providers import create_provider

_JSON_RE = re.compile(r"\{[\s\S]*\}")
_DIFF_LIMIT = 50_000
_OUTPUT_LIMIT = 10_000


class EvaluatorRunner:
    """Run mechanical and semantic evaluation for a task."""

    def __init__(self, config: EvaluatorConfig, project_config: OrchestratorConfig) -> None:
        self.config = config
        self.project_config = project_config

    def evaluate(
        self,
        task: TaskSpec,
        gen_output: GeneratorOutput,
        worktree: Path,
    ) -> EvalResult:
        if task.skip_qa:
            return EvalResult(
                score=100,
                passed=True,
                retryable=False,
                feedback=EvalFeedback(summary="QA skipped for this task"),
            )

        module_path = worktree
        if task.module not in ("root", "*"):
            module_path = (worktree / self.project_config.get_module(task.module).path).resolve()

        qa_results = self._run_qa_gates(
            checks=task.qa_checks,
            worktree=worktree,
            module_name=task.module,
            module_path=module_path,
            task_output=gen_output.output,
            skip_gates=set(task.skip_gates),
        )

        failed = [result for result in qa_results if not result.passed]
        if failed and all(not result.retryable for result in failed):
            return EvalResult(
                score=0,
                passed=False,
                retryable=False,
                feedback=EvalFeedback(summary="All failing QA gates were marked non-retryable"),
                qa_results=qa_results,
            )

        eval_result = self._run_eval_agent(task, gen_output, qa_results, worktree)
        eval_result.qa_results = qa_results
        if failed:
            eval_result.retryable = any(result.retryable for result in failed)
        return eval_result

    def _run_qa_gates(
        self,
        checks: list[QACheck],
        worktree: Path,
        module_name: str,
        module_path: Path,
        task_output: str,
        skip_gates: set[str],
    ) -> list[QAResult]:
        active_checks = [check for check in checks if check.gate not in skip_gates]
        if not active_checks:
            return []

        def _run(check: QACheck) -> QAResult:
            return run_qa_gate(
                check=check,
                project_root=worktree,
                module_name=module_name,
                task_output=task_output,
                custom_gates=self.project_config.qa_gates.custom,
                module_path=module_path,
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(active_checks), 4)) as pool:
            return list(pool.map(_run, active_checks))

    def _build_eval_prompt(
        self,
        task: TaskSpec,
        gen_output: GeneratorOutput,
        qa_results: list[QAResult],
    ) -> str:
        qa_summary = "\n".join(
            f"- {qa.gate}: {'PASS' if qa.passed else 'FAIL'} :: {qa.output[:500]}"
            for qa in qa_results
        )
        acceptance = task.acceptance_criteria or task.description
        evaluator_prompt = task.evaluator_prompt or "Score the implementation against the criteria."
        parts = [
            self.config.prompt_prefix.strip(),
            "## Scoring Rubric\n"
            "Score 90-100: All acceptance criteria met, code clean, tests pass\n"
            "Score 70-89: Most criteria met, minor issues\n"
            "Score 50-69: Some criteria met, notable gaps\n"
            "Score 30-49: Significant gaps, multiple failing criteria\n"
            "Score 0-29: Fundamental issues, wrong approach",
            f"## Acceptance Criteria\n\n{acceptance}",
            f"## Evaluator Instructions\n\n{evaluator_prompt}",
            f"## Git Diff\n\n{gen_output.diff[:_DIFF_LIMIT]}",
            f"## Generator Output\n\n{gen_output.output[:_OUTPUT_LIMIT]}",
            f"## QA Results\n\n{qa_summary or '- none'}",
            (
                "## Output Format\n\n"
                'Return valid JSON: {"score": 0-100, "feedback": {"summary": "", '
                '"specific_errors": [], "files_to_check": [], "remediation_steps": [], '
                '"failed_criteria": [], "evidence": "", "missing_behaviors": []}}'
            ),
        ]
        return "\n\n".join(part for part in parts if part)

    def _run_eval_agent(
        self,
        task: TaskSpec,
        gen_output: GeneratorOutput,
        qa_results: list[QAResult],
        worktree: Path,
    ) -> EvalResult:
        prompt = self._build_eval_prompt(task, gen_output, qa_results)
        provider = create_provider(self.config)
        try:
            result = provider.dispatch_simple(task.module, worktree, prompt)
        except Exception as exc:
            return EvalResult(
                score=0,
                passed=False,
                retryable=True,
                feedback=EvalFeedback(summary=f"Evaluator error: {exc}"),
            )

        if not result.success:
            return EvalResult(
                score=0,
                passed=False,
                retryable=True,
                feedback=EvalFeedback(summary=result.output[:500] or "Evaluator failed"),
                cost_usd=result.cost_usd,
                duration_seconds=result.duration_seconds,
            )

        try:
            match = _JSON_RE.search(result.output)
            payload = json.loads(match.group(0) if match else result.output)
            feedback_payload = payload.get("feedback", {})
            score = int(payload.get("score", 0))
        except Exception:
            return EvalResult(
                score=0,
                passed=False,
                retryable=True,
                feedback=EvalFeedback(
                    summary="Failed to parse evaluator output",
                    evidence=result.output[:500],
                ),
                cost_usd=result.cost_usd,
                duration_seconds=result.duration_seconds,
            )

        return EvalResult(
            score=score,
            passed=score >= self.config.pass_threshold,
            retryable=True,
            feedback=EvalFeedback(
                summary=feedback_payload.get("summary", ""),
                specific_errors=feedback_payload.get("specific_errors", []),
                files_to_check=feedback_payload.get("files_to_check", []),
                remediation_steps=feedback_payload.get("remediation_steps", []),
                failed_criteria=feedback_payload.get("failed_criteria", []),
                evidence=feedback_payload.get("evidence", ""),
                missing_behaviors=feedback_payload.get("missing_behaviors", []),
            ),
            cost_usd=result.cost_usd,
            duration_seconds=result.duration_seconds,
        )
