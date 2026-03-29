# TODOS

## Duplicate config field declarations
**What:** Remove duplicate `generator` and `evaluator` field declarations in `config.py:180-184`.
**Why:** Merge conflict artifact. Pydantic silently uses the last declaration, but duplicates are confusing and could mask issues if the two declarations diverge.
**Context:** `OrchestratorConfig` declares `generator: GeneratorConfig` on both line 180 and 183, `evaluator: EvaluatorConfig` on both line 181 and 184. Delete lines 180-181. Zero risk, one-line fix.
**Depends on:** Nothing.
