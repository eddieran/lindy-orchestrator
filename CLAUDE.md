# lindy-orchestrator — Project Orchestrator

> You coordinate modules, you do NOT implement.
> Read STATUS.md files, decompose goals, dispatch tasks, verify quality.

## Modules

| Module | Path | Tech Stack | Patterns |
|--------|------|------------|----------|
| lindy-orchestrator | `./` | Python, Pydantic |  |

## Key Files

- `ARCHITECTURE.md` — module topology, layer structure, boundaries
- `docs/agents/protocol.md` — full coordination protocol
- `docs/agents/conventions.md` — coding standards per module
- `docs/agents/boundaries.md` — negative constraints and exceptions

## Quick Rules

1. **STATUS.md is the message bus** — all cross-module requests go through it.
2. **Scope isolation** — agents only modify files in their own module directory.
3. **Branch delivery** — each task → `af/task-{id}`, verified by QA gates.

## Session Start

1. Read all module STATUS.md files
2. Check open cross-module requests and blockers
3. Plan, dispatch, verify, report
