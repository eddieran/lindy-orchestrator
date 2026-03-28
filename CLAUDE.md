# lindy-orchestrator

Git-native multi-agent orchestration framework for autonomous project execution.

## Repo layout

```text
lindy-orchestrator/
├── src/lindy_orchestrator/    # Main package
│   ├── cli.py                 # CLI entry (typer)
│   ├── planner.py             # Task DAG decomposition
│   ├── executor.py            # Agent dispatch
│   ├── qa_gate.py             # Pluggable QA validation
│   └── ...
├── tests/                     # pytest test suite
├── docs/                      # Architecture and usage docs
├── scripts/                   # Preflight and setup scripts
├── .codex/skills/             # Codex agent skills
├── pyproject.toml             # Project config (uv/hatch)
└── WORKFLOW.md                # Symphony orchestration config
```

## Quick reference

```bash
# Setup
uv sync --extra dev --frozen

# Test
pytest tests/ -x -q --tb=short

# Lint
ruff check src/ tests/

# Format check
ruff format --check src/ tests/

# CLI
lindy-orchestrate --help
```

## Conventions

- Python 3.11+, managed with uv
- Type hints on all public functions
- Conventional commits: `<type>(<scope>): <description>`
- Branch naming: `lo/<issue-id>` or `lo/<descriptive-name>`
- PR label: `symphony` for orchestrated PRs

## Read first

- This file for repo orientation
- `STATUS.md` for active work and metrics
- `README.md` for full documentation
- `CONTRIBUTING.md` for development workflow
