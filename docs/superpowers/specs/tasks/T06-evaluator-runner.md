---
task: T6
title: Evaluator Runner
depends_on: [2b]
status: pending
---

## T6: Evaluator Runner

**ID:** 6
**Depends on:** [2b]
**Module:** `src/lindy_orchestrator/evaluator_runner.py` (new)

### Description

Create `EvaluatorRunner` — two-phase evaluation: mechanical QA gates (parallel), then agent-based judgment with scoring rubric. Uses extended EvalFeedback for rich retry feedback. Handles timeout/error gracefully.

### Generator Prompt

Create `src/lindy_orchestrator/evaluator_runner.py`:

```python
class EvaluatorRunner:
    def __init__(self, config: EvaluatorConfig, project_config: OrchestratorConfig): ...
    def evaluate(self, task: TaskSpec, gen_output: GeneratorOutput,
                 worktree: Path) -> EvalResult: ...
    def _run_qa_gates(self, checks: list[QACheck], worktree: Path,
                      project_root: Path, module_name: str,
                      module_path: Path | None) -> list[QAResult]: ...
    def _run_eval_agent(self, task: TaskSpec, gen_output: GeneratorOutput,
                        qa_results: list[QAResult]) -> EvalResult: ...
    def _build_eval_prompt(self, task: TaskSpec, gen_output: GeneratorOutput,
                           qa_results: list[QAResult]) -> str: ...
```

Key behaviors:
1. `evaluate()`:
   - If `task.skip_qa`: return EvalResult(score=100, passed=True, retryable=False)
   - Run `_run_qa_gates()` — parallel via ThreadPoolExecutor
   - Check retryability: if all QA failures have `retryable=False` (pre-existing), return EvalResult(score=0, passed=False, retryable=False)
   - Run `_run_eval_agent()` — intelligent assessment
   - Return combined EvalResult

2. `_run_qa_gates()`:
   - Reuse existing `run_qa_gate()` from `qa/__init__.py`
   - Filter by `task.skip_gates`
   - Run all checks in parallel with ThreadPoolExecutor
   - Return list of QAResult

3. `_build_eval_prompt()`:
   - Start with `config.prompt_prefix` (from YAML)
   - Append scoring rubric:
     ```
     Score 90-100: All acceptance criteria met, code clean, tests pass
     Score 70-89: Most criteria met, minor issues
     Score 50-69: Some criteria met, notable gaps
     Score 30-49: Significant gaps, multiple failing criteria
     Score 0-29: Fundamental issues, wrong approach
     ```
   - Append `task.acceptance_criteria`
   - Append `task.evaluator_prompt`
   - Append `gen_output.diff` (truncated to 50K chars)
   - Append `gen_output.output` (truncated to 10K chars)
   - Append QA gate results summary (gate name, pass/fail, output truncated)
   - Instruct output format:
     ```json
     {"score": 0-100, "feedback": {"summary": "...", "specific_errors": [...], "files_to_check": [...], "remediation_steps": [...], "failed_criteria": [...], "evidence": "...", "missing_behaviors": [...]}}
     ```
   - **MUST NOT include `task.generator_prompt`**

4. `_run_eval_agent()`:
   - Create provider from `config.to_role_provider_config()`
   - Call `provider.dispatch_simple(prompt=eval_prompt)`
   - Parse JSON verdict
   - **Compute `passed` in code**: `score >= config.pass_threshold` — do NOT trust model's boolean
   - Compute `retryable` from QA results: if all failures are pre-existing (`qa_result.retryable == False`), set `retryable = False`
   - On JSON parse failure: return EvalResult(score=0, retryable=True, feedback=EvalFeedback(summary="Failed to parse evaluator output", evidence=raw_output[:500]))
   - On provider timeout/error: return EvalResult(score=0, retryable=True, feedback=EvalFeedback(summary=f"Evaluator timed out after {config.timeout_seconds}s"))

5. Write `tests/test_evaluator_runner.py`:
   - QA gates run in parallel (mock gates, check ThreadPoolExecutor usage)
   - Eval prompt contains acceptance_criteria but NOT generator_prompt
   - Score=45 with threshold=80 → passed=False
   - Score=85 with threshold=80 → passed=True
   - `passed` computed in code, not from model output
   - All QA retryable=False → EvalResult.retryable=False
   - JSON parse failure → score=0, retryable=True
   - Provider timeout → score=0, retryable=True
   - skip_qa → score=100 immediately
   - skip_gates filters correctly
   - Provider created from EvaluatorConfig

### Acceptance Criteria

- `EvaluatorRunner.evaluate()` returns EvalResult with score, passed, retryable, feedback, qa_results
- QA gates execute in parallel, filtered by skip_gates
- Eval prompt contains acceptance_criteria + evaluator_prompt + diff + qa_results + rubric
- Eval prompt does NOT contain generator_prompt (test assertion)
- `passed` computed from `score >= threshold` in code
- `retryable` derived from QA gate retryability
- Timeout/error → graceful fallback with retryable=True
- Provider created from `EvaluatorConfig.provider`
- ≥16 unit tests

### Evaluator Prompt

Verify: (1) build eval prompt for a TaskSpec with generator_prompt="implement X" — assert that string does NOT appear, (2) acceptance_criteria IS in the prompt, (3) scoring rubric IS in the prompt, (4) mock evaluator returning `{"score": 45}` → passed=False (threshold 80), (5) mock evaluator returning `{"score": 85}` → passed=True, (6) timeout → EvalResult with score=0 and retryable=True, (7) all QA retryable=False → EvalResult.retryable=False.

### QA Checks

- gate: command_check
  command: "uv run python -m pytest tests/test_evaluator_runner.py tests/ -x -q --tb=short"
  timeout: 120
