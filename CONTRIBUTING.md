# Contributing to lindy-orchestrator

## Development Setup

```bash
git clone https://github.com/eddieran/lindy-orchestrator.git
cd lindy-orchestrator
pip install -e ".[dev]"
```

Requires Python 3.11+.

For API-mode planning (optional):

```bash
pip install -e ".[api]"
export ANTHROPIC_API_KEY=sk-...
```

## Running Tests

```bash
pytest tests/ -v
```

With coverage:

```bash
pytest tests/ --cov=src/lindy_orchestrator --cov-report=term-missing
```

Coverage threshold is 70% (configured in `pyproject.toml`). All tests must pass before
submitting a PR. CI runs tests on Python 3.11, 3.12, and 3.13.

## Code Style

This project uses [Ruff](https://docs.astral.sh/ruff/) for linting and formatting:

```bash
ruff check src/ tests/
ruff format src/ tests/
```

Configuration in `pyproject.toml`:
- Target: Python 3.11
- Line length: 100

Additional conventions:
- Type hints on all function signatures
- PEP 8 naming conventions
- `pathlib.Path` over `os.path`
- Pydantic models for data validation (`model_validate()`, not deprecated `parse_obj()`)
- Dataclasses for value objects, Pydantic for config/validation
- `from __future__ import annotations` in every module

## Pull Request Process

1. Fork the repo and create a feature branch from `main`.
2. Make your changes. Add tests for new functionality.
3. Run `pytest tests/ -v` and `ruff check src/ tests/` locally.
4. Submit a PR against `main` with a clear description of the change.

## Architecture Overview

See [ARCHITECTURE.md](ARCHITECTURE.md) for the full component diagram, data flow,
layer structure per package, and boundary rules.

## Project Structure

```
src/lindy_orchestrator/
    __init__.py
    cli.py                          # Main CLI entry point (Typer)
    cli_ext.py                      # Extension commands (gc, scan, validate, issues, run-issue, mailbox)
    cli_helpers.py                  # Shared CLI utilities
    cli_init.py                     # init command
    cli_onboard.py                  # onboard command (guided project setup)
    cli_onboard_helpers.py          # Onboard helper functions
    cli_scaffold.py                 # Scaffold command
    cli_status.py                   # status command (module health overview)
    codex_dispatcher.py             # Codex CLI subprocess management
    config.py                       # Configuration loading (Pydantic + YAML)
    dag.py                          # DAG visualization (ASCII tree with status icons)
    dashboard.py                    # Live DAG dashboard (Rich Live panel)
    dispatcher.py                   # Claude CLI subprocess management
    gc.py                           # Garbage collection (stale branches, sessions, logs)
    hooks.py                        # Central event hook system (14 event types)
    logger.py                       # JSONL append-only action logger
    mailbox.py                      # JSONL-based inter-agent mailbox
    models.py                       # Core data models (TaskPlan, TaskItem, etc.)
    planner.py                      # Goal decomposition via LLM
    prompts.py                      # Prompt template rendering
    reporter.py                     # Rich terminal output and summary reports
    scheduler.py                    # DAG-based parallel task execution
    scheduler_helpers.py            # Scheduler helper functions
    session.py                      # Session state persistence
    worktree.py                     # Git worktree utilities for parallel isolation
    discovery/
        __init__.py
        analyzer.py                 # Static project analyzer
        analyzer_helpers.py         # Analyzer helper functions
        generator.py                # Artifact generator (orchestrator.yaml, CLAUDE.md, etc.)
        interview.py                # Interactive discovery interview
        templates/
            __init__.py
            agent_docs.py           # Agent documentation templates
            architecture_md.py      # ARCHITECTURE.md template
            contracts_md.py         # CONTRACTS.md template
            module_claude_md.py     # Per-module CLAUDE.md template
            root_claude_md.py       # Root CLAUDE.md template
    entropy/
        __init__.py
        scanner.py                  # Entropy scanner (drift, contracts, quality)
        scanner_helpers.py          # Scanner grading and formatting
        scanner_types.py            # ScanFinding, ModuleGrade, ScanReport
    providers/
        __init__.py                 # Provider factory (create_provider)
        base.py                     # DispatchProvider Protocol
        claude_cli.py               # Claude Code CLI provider
        codex_cli.py                # OpenAI Codex CLI provider
    qa/
        __init__.py                 # QA gate registry and runner
        agent_check.py              # Agent-based QA check
        ci_check.py                 # CI pipeline check (gh CLI)
        command_check.py            # Custom shell command gate
        feedback.py                 # Structured QA feedback (pytest/ruff/tsc parsers)
        layer_check.py              # Intra-module layer ordering check
        structural_check.py         # File size, sensitive files, import boundaries
    status/
        __init__.py
        parser.py                   # STATUS.md parser
        templates.py                # STATUS.md scaffold templates
        writer.py                   # STATUS.md writer
    trackers/
        __init__.py
        base.py                     # TrackerProvider Protocol
        factory.py                  # Tracker factory
        github_issues.py            # GitHub Issues provider (gh CLI)
tests/                              # Test suite (pytest)
docs/                               # Usage guides and agent reference
```

## Reporting Issues

Open an issue on [GitHub](https://github.com/eddieran/lindy-orchestrator/issues) with:
- Steps to reproduce
- Expected vs actual behavior
- Python version and OS

## License

By contributing, you agree that your contributions will be licensed under the [MIT License](LICENSE).
