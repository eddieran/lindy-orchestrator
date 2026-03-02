# lindy-orchestrator

Lightweight, git-native multi-agent orchestration framework for autonomous project execution.

## What it does

`lindy-orchestrator` decomposes natural-language goals into dependency-ordered tasks, dispatches them to LLM-powered agents working in isolated module directories, validates results through pluggable QA gates, and coordinates everything through markdown files and git.

**No database, no shared memory, no infrastructure — just git, markdown, and your existing project.**

## Quick Start

```bash
# Install
pip install lindy-orchestrator

# Initialize in your project
cd my-project
lindy-orchestrate init

# Review the generated config
cat orchestrator.yaml

# Dry-run a goal (no dispatches)
lindy-orchestrate plan "Add user authentication"

# Execute a goal
lindy-orchestrate run "Add user authentication"
```

## How it works

```
User provides goal (natural language)
    ↓
Planner (LLM) decomposes → JSON task plan with dependency DAG
    ↓
Scheduler dispatches tasks in parallel (respecting dependencies)
    ↓
Each task: Agent works in module dir → commits to branch → pushes
    ↓
QA gates validate each task (CI check, command check, agent check)
    ↓
Report generated with results
```

## Configuration

`orchestrator.yaml` in your project root:

```yaml
project:
  name: "my-project"
  branch_prefix: "af"

modules:
  - name: backend
    path: backend/
    repo: myorg/my-backend
    ci_workflow: ci.yml

  - name: frontend
    path: frontend/

planner:
  mode: cli    # cli (claude -p) or api (Anthropic SDK)

safety:
  max_retries_per_task: 2
  max_parallel: 3
```

## CLI Commands

| Command | Description |
|---------|-------------|
| `lindy-orchestrate init` | Scaffold onto existing project |
| `lindy-orchestrate run <goal>` | Execute a goal |
| `lindy-orchestrate plan <goal>` | Generate plan without executing |
| `lindy-orchestrate status` | Show module statuses |
| `lindy-orchestrate logs` | Show action logs |
| `lindy-orchestrate resume` | Resume previous session |
| `lindy-orchestrate validate` | Validate config and STATUS.md |

## Key Concepts

- **Modules**: Independent directories in your project (services, packages, etc.)
- **STATUS.md**: Markdown file tracking each module's state — human-readable, git-diffable
- **QA Gates**: Pluggable validation (CI polling, command checks, agent-based validation)
- **Sessions**: Persistent execution state for pause/resume
- **Branch-based delivery**: Each task creates a reviewable, CI-gated branch

## License

MIT
