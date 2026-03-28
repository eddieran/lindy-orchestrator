"""Evaluator runner for QA gates plus rubric-based agent scoring."""

from __future__ import annotations

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from .config import EvaluatorConfig, OrchestratorConfig
from .models import EvalFeedback, EvalResult, GeneratorOutput, QACheck, QAResult, TaskSpec
from .providers import create_provider
from .qa import run_qa_gate

_DIFF_LIMIT = 50_000
_OUTPUT_LIMIT = 10_000
_QA_OUTPUT_LIMIT = 1_000


class EvaluatorRunner:
    """Run mechanical QA checks before evaluator-agent scoring."""

    def __init__(self, config: EvaluatorConfig, project_config: OrchestratorConfig):
        self.config = config
        self.project_config = project_config

    def evaluate(self, task: TaskSpec, gen_output: GeneratorOutput, worktree: Path) -> EvalResult:
        """Evaluate a completed generator attempt."""
        if task.skip_qa:
            return EvalResult(score=100, passed=True, retryable=False)

        project_root = worktree.resolve()
        module_path = self._resolve_module_path(task, worktree)
        qa_results = self._run_qa_gates(
            checks=task.qa_checks,
            worktree=worktree,
            project_root=project_root,
            module_name=task.module,
            module_path=module_path,
            skip_gates=task.skip_gates,
            task_output=gen_output.output,
        )

        failed_qa = [result for result in qa_results if not result.passed]
        if failed_qa and all(not result.retryable for result in failed_qa):
            return EvalResult(
                score=0,
                passed=False,
                retryable=False,
                feedback=EvalFeedback(
                    summary="Only non-retryable QA failures remain",
                    specific_errors=[f"{result.gate}: {result.output}" for result in failed_qa],
                    evidence=self._summarize_qa_results(qa_results),
                ),
                qa_results=qa_results,
            )

        return self._run_eval_agent(task, gen_output, qa_results, worktree=worktree)

    def _run_qa_gates(
        self,
        checks: list[QACheck],
        worktree: Path,
        project_root: Path,
        module_name: str,
        module_path: Path | None,
        skip_gates: list[str] | None = None,
        task_output: str = "",
    ) -> list[QAResult]:
        """Run QA gates in parallel and return their results."""
        del worktree  # the worktree is represented by project_root/module_path for gate execution

        skipped = set(skip_gates or [])
        selected_checks = [check for check in checks if check.gate not in skipped]
        if not selected_checks:
            return []

        qa_module = self.project_config.qa_module()
        results: list[QAResult] = []

        def _run(check: QACheck) -> QAResult:
            return run_qa_gate(
                check=check,
                project_root=project_root,
                module_name=module_name,
                task_output=task_output,
                custom_gates=self.project_config.qa_gates.custom,
                dispatcher_config=self.project_config.dispatcher,
                qa_module=qa_module,
                module_path=module_path,
            )

        with ThreadPoolExecutor(max_workers=min(len(selected_checks), 4)) as pool:
            futures = {
                pool.submit(_run, check): index for index, check in enumerate(selected_checks)
            }
            ordered_results: dict[int, QAResult] = {}
            for future in as_completed(futures):
                ordered_results[futures[future]] = future.result()

        for index in range(len(selected_checks)):
            results.append(ordered_results[index])
        return results

    def _run_eval_agent(
        self,
        task: TaskSpec,
        gen_output: GeneratorOutput,
        qa_results: list[QAResult],
        worktree: Path | None = None,
    ) -> EvalResult:
        """Call the evaluator agent and parse its verdict."""
        prompt = self._build_eval_prompt(task, gen_output, qa_results)
        retryable = self._qa_retryable(qa_results)
        provider = create_provider(self.config)
        working_dir = worktree.resolve() if worktree is not None else self.project_config.root
        started = time.monotonic()

        try:
            dispatch_result = provider.dispatch_simple(
                module=task.module,
                working_dir=working_dir,
                prompt=prompt,
            )
        except TimeoutError:
            return EvalResult(
                score=0,
                passed=False,
                retryable=retryable,
                feedback=EvalFeedback(
                    summary=f"Evaluator timed out after {self.config.timeout_seconds}s"
                ),
                qa_results=qa_results,
                duration_seconds=time.monotonic() - started,
            )
        except Exception as exc:
            return EvalResult(
                score=0,
                passed=False,
                retryable=retryable,
                feedback=EvalFeedback(summary=f"Evaluator failed: {exc}"),
                qa_results=qa_results,
                duration_seconds=time.monotonic() - started,
            )

        if not dispatch_result.success:
            return EvalResult(
                score=0,
                passed=False,
                retryable=retryable,
                feedback=EvalFeedback(
                    summary="Evaluator failed", evidence=dispatch_result.output[:500]
                ),
                qa_results=qa_results,
                cost_usd=dispatch_result.cost_usd,
                duration_seconds=dispatch_result.duration_seconds,
            )

        try:
            payload = self._parse_json_payload(dispatch_result.output)
        except ValueError:
            return EvalResult(
                score=0,
                passed=False,
                retryable=retryable,
                feedback=EvalFeedback(
                    summary="Failed to parse evaluator output",
                    evidence=dispatch_result.output[:500],
                ),
                qa_results=qa_results,
                cost_usd=dispatch_result.cost_usd,
                duration_seconds=dispatch_result.duration_seconds,
            )

        score = self._coerce_score(payload.get("score", 0))
        feedback_data = payload.get("feedback") or {}
        feedback = EvalFeedback(
            summary=str(feedback_data.get("summary", "")),
            specific_errors=self._coerce_list(feedback_data.get("specific_errors")),
            files_to_check=self._coerce_list(feedback_data.get("files_to_check")),
            remediation_steps=self._coerce_list(feedback_data.get("remediation_steps")),
            failed_criteria=self._coerce_list(feedback_data.get("failed_criteria")),
            evidence=str(feedback_data.get("evidence", "")),
            missing_behaviors=self._coerce_list(feedback_data.get("missing_behaviors")),
        )
        return EvalResult(
            score=score,
            passed=score >= self.config.pass_threshold,
            retryable=retryable,
            feedback=feedback,
            qa_results=qa_results,
            cost_usd=dispatch_result.cost_usd,
            duration_seconds=dispatch_result.duration_seconds,
        )

    def _build_eval_prompt(
        self, task: TaskSpec, gen_output: GeneratorOutput, qa_results: list[QAResult]
    ) -> str:
        """Build the evaluator prompt without leaking generator-only instructions."""
        sections: list[str] = []
        if self.config.prompt_prefix.strip():
            sections.append(self.config.prompt_prefix.strip())

        sections.append(
            "You are the evaluator for an orchestration task. Review the delivered work and "
            "return JSON only."
        )
        sections.append(
            "\n".join(
                [
                    "## Scoring Rubric",
                    "Score 90-100: All acceptance criteria met, code clean, tests pass",
                    "Score 70-89: Most criteria met, minor issues",
                    "Score 50-69: Some criteria met, notable gaps",
                    "Score 30-49: Significant gaps, multiple failing criteria",
                    "Score 0-29: Fundamental issues, wrong approach",
                ]
            )
        )
        sections.append(
            "## Acceptance Criteria\n"
            + (task.acceptance_criteria.strip() or "No acceptance criteria were provided.")
        )

        if task.evaluator_prompt.strip():
            sections.append(f"## Evaluator Instructions\n{task.evaluator_prompt.strip()}")

        sections.append(f"## QA Gate Results\n{self._summarize_qa_results(qa_results)}")

        if gen_output.diff:
            sections.append(
                "## Git Diff\n```diff\n" + self._truncate(gen_output.diff, _DIFF_LIMIT) + "\n```"
            )

        if gen_output.output:
            sections.append(
                "## Generator Output\n```\n"
                + self._truncate(gen_output.output, _OUTPUT_LIMIT)
                + "\n```"
            )

        sections.append(
            "\n".join(
                [
                    "Return JSON with this exact shape:",
                    "```json",
                    '{"score": 0, "feedback": {"summary": "", "specific_errors": [], '
                    '"files_to_check": [], "remediation_steps": [], "failed_criteria": [], '
                    '"evidence": "", "missing_behaviors": []}}',
                    "```",
                ]
            )
        )
        return "\n\n".join(sections)

    def _resolve_module_path(self, task: TaskSpec, worktree: Path) -> Path | None:
        if task.module in {"root", "*"}:
            return worktree.resolve()
        try:
            module = self.project_config.get_module(task.module)
        except ValueError:
            return (worktree / task.module).resolve()
        return (worktree / module.path).resolve()

    def _qa_retryable(self, qa_results: list[QAResult]) -> bool:
        failed = [result for result in qa_results if not result.passed]
        return not failed or any(result.retryable for result in failed)

    def _summarize_qa_results(self, qa_results: list[QAResult]) -> str:
        if not qa_results:
            return "No QA gates were run."

        lines = []
        for result in qa_results:
            status = "PASS" if result.passed else "FAIL"
            lines.append(f"- {result.gate}: {status} (retryable={result.retryable})")
            if result.output:
                lines.append(f"  Output: {self._truncate(result.output, _QA_OUTPUT_LIMIT)}")
        return "\n".join(lines)

    def _parse_json_payload(self, raw_output: str) -> dict:
        text = raw_output.strip()
        if not text:
            raise ValueError("empty evaluator output")

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            match = re.search(r"\{[\s\S]*\}", text)
            if not match:
                raise ValueError("no JSON payload found") from None
            return json.loads(match.group(0))

    def _coerce_score(self, raw_score: object) -> int:
        try:
            return max(0, min(100, int(raw_score)))
        except (TypeError, ValueError):
            return 0

    def _coerce_list(self, value: object) -> list[str]:
        if not value:
            return []
        if isinstance(value, list):
            return [str(item) for item in value if str(item).strip()]
        return [str(value)]

    def _truncate(self, value: str, limit: int) -> str:
        if len(value) <= limit:
            return value
        return value[: limit - 18] + "\n...[truncated]..."
