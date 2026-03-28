# Pipeline Refactor — Task DAG

**Spec:** `../2026-03-28-pipeline-architecture-design.md`

## DAG

```
Level 0:  T1 (models + serialization)
              |
Level 1:  T2 (config schema)
              |
         +----+----+
Level 2: T2b      T3                 parallel
         (providers)(soft-rm)
         +----+----+
              |
         +----+----+----+
Level 3:  T4  T5   T6               parallel
         (plan)(gen)(eval)
              |
Level 4:  T7 (orchestrator)
              |
         +----+----+
Level 5:  T8       T9               parallel
         (viz)    (CLI+onboard)
              |
Level 6:  T10 (integration tests)
              |
Level 7:  T11 (e2e tests)
              |
Level 8:  T12 (hard delete + PR)
```

## Tasks

| ID | File | Title | Depends On | Status |
|----|------|-------|------------|--------|
| T1 | [T01-data-models.md](T01-data-models.md) | Data Models + Serialization | none | pending |
| T2 | [T02-config-schema.md](T02-config-schema.md) | Configuration Schema | T1 | pending |
| T2b | [T02b-provider-factory.md](T02b-provider-factory.md) | Provider Factory Refactor | T2 | pending |
| T3 | [T03-soft-deprecation.md](T03-soft-deprecation.md) | Soft Feature Deprecation | T2 | pending |
| T4 | [T04-planner-runner.md](T04-planner-runner.md) | Planner Runner | T2b | pending |
| T5 | [T05-generator-runner.md](T05-generator-runner.md) | Generator Runner | T2b | pending |
| T6 | [T06-evaluator-runner.md](T06-evaluator-runner.md) | Evaluator Runner | T2b | pending |
| T7 | [T07-orchestrator.md](T07-orchestrator.md) | Orchestrator | T3, T4, T5, T6 | pending |
| T8 | [T08-visualization.md](T08-visualization.md) | Visualization Update | T7 | pending |
| T9 | [T09-cli-wiring.md](T09-cli-wiring.md) | CLI Wiring + Non-Runtime Consumers | T7 | pending |
| T10 | [T10-integration-tests.md](T10-integration-tests.md) | Integration Tests | T8, T9 | pending |
| T11 | [T11-e2e-tests.md](T11-e2e-tests.md) | End-to-End Tests | T10 | pending |
| T12 | [T12-cleanup-pr.md](T12-cleanup-pr.md) | Hard Delete + Cleanup + PR | T11 | pending |

## Execution

Each task is dispatched to a subagent. The subagent reads the task file for its full instructions (Generator Prompt), then executes. After completion, QA Checks are run, and the Evaluator Prompt is used to verify acceptance criteria.

**Parallel groups:**
- Level 2: T2b + T3
- Level 3: T4 + T5 + T6
- Level 5: T8 + T9
