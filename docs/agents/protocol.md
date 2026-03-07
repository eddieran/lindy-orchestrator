# Coordination Protocol — lindy-orchestrator

> Detailed rules for multi-agent coordination. The root CLAUDE.md
> contains a summary; this document is the full reference.

## STATUS.md as Message Bus

Each module has a `STATUS.md` file that tracks:
- **Module Metadata**: health (GREEN/YELLOW/RED), last update, session ID
- **Active Work**: current tasks with status and blockers
- **Completed (Recent)**: recently finished tasks
- **Cross-Module Requests**: OPEN/IN_PROGRESS/DONE requests between modules
- **Cross-Module Deliverables**: completed outputs for other modules
- **Key Metrics**: module-specific health indicators
- **Blockers**: issues that prevent progress

### Request Flow
1. Module A needs work from Module B → A creates a Cross-Module Request in A's STATUS.md
2. Orchestrator picks up open requests during planning
3. Module B executes the request and records a deliverable
4. Module A reads the deliverable and marks the request DONE

## Branch-Based Delivery

- Each task produces a branch: `af/task-{id}`
- Agents commit and push to their branch
- The orchestrator verifies branch existence and commit count
- Branches are merged after QA gates pass

## QA Gates

Every task is verified by quality checks before marking complete:
- **structural_check**: file size limits, sensitive file detection, import boundaries
- **layer_check**: intra-module layer ordering (parsed from ARCHITECTURE.md)
- **ci_check**: CI pipeline pass/fail
- **command_check**: custom commands (test suites, linters)
- **agent_check**: dispatches a QA agent for complex validation

QA failures trigger automatic retry with structured feedback.
Maximum retries: configurable per project (default: 2).

## Cross-Module Request Protocol

When you need work from another module:
1. Add an entry to your STATUS.md "Cross-Module Requests" table
2. Set status to OPEN
3. Include priority (P0=critical, P1=high, P2=normal)
4. The orchestrator will pick it up in the next planning cycle

Do NOT directly modify files in other modules.

## ARCHITECTURE.md

The structural map at the project root defines:
- Module topology and tech stacks
- Dependency directions between modules
- Negative boundaries (what does NOT belong where)
- Layer structure per module

Read ARCHITECTURE.md before planning any cross-module work.
