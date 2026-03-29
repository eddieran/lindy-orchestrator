# Contributing to lindy-orchestrator

## Development Setup

```bash
git clone https://github.com/eddieran/lindy-orchestrator.git
cd lindy-orchestrator

# With uv (recommended)
uv sync --extra dev --frozen
uv run python -m pytest tests/ -x -q --tb=short

# With pip
pip install -e ".[dev]"
pytest tests/ -x -q --tb=short
```

Requires Python 3.11+.

## Running Tests

```bash
# Quick run
uv run python -m pytest tests/ -x -q --tb=short

# Verbose
uv run python -m pytest tests/ -v

# With coverage
uv run python -m pytest tests/ --cov=lindy_orchestrator --cov-report=term-missing
```

Coverage threshold is 70% (configured in `pyproject.toml`). All tests must pass before
submitting a PR. CI runs tests on Python 3.11, 3.12, and 3.13.

## Code Style

This project uses [Ruff](https://docs.astral.sh/ruff/) for linting and formatting:

```bash
uv run ruff check src/ tests/
uv run ruff format src/ tests/
```

Configuration in `pyproject.toml`:
- Target: Python 3.11
- Line length: 100

Additional conventions:
- Type hints on all function signatures
- PEP 8 naming conventions
- `pathlib.Path` over `os.path`
- Pydantic models for config/validation, dataclasses for value objects
- `from __future__ import annotations` in every module

## Pull Request Process

1. Fork the repo and create a feature branch from `main`.
2. Make your changes. Add tests for new functionality.
3. Run tests and lint locally.
4. Submit a PR against `main` with a clear description.

## Project Structure

```
src/lindy_orchestrator/
    __init__.py
    cli.py                          # Main CLI entry point
    cli_ext.py                      # Extension commands (gc, scan, validate, stats, clear)
    cli_helpers.py                  # Shared CLI utilities
    cli_config.py                   # config command (global/local provider settings)
    cli_onboard.py                  # onboard command (guided project setup)
    cli_status.py                   # status command (module health overview)
    cli_stats.py                    # stats command (execution metrics)
    cli_clear.py                    # clear command (remove generated files)
    command_queue.py                # Thread-safe interactive controls (pause/skip/force-pass)
    config.py                       # Configuration loading (Pydantic + YAML)
    dag.py                          # DAG visualization (ASCII tree with status icons)
    dashboard.py                    # Live DAG dashboard (Rich Live panel)
    dispatch_core.py                # Streaming dispatch with stall detection
    evaluator_runner.py             # Evaluator role: QA gates + agent scoring
    gc.py                           # Garbage collection (stale branches, sessions, logs)
    generator_runner.py             # Generator role: code dispatch with context isolation
    hooks.py                        # Central event hook system
    logger.py                       # JSONL append-only action logger
    metrics.py                      # Metrics collection from hook events
    models.py                       # Core data models (TaskSpec, TaskPlan, etc.)
    orchestrator.py                 # Pipeline coordinator (Generator -> Evaluator loop)
    orchestrator_helpers.py         # Orchestrator helper utilities
    planner_runner.py               # Planner role: goal decomposition via LLM
    prompts.py                      # Prompt template rendering
    reporter.py                     # Rich terminal output and summary reports
    scheduler_helpers.py            # Legacy scheduler helper functions
    session.py                      # Session state persistence
    worktree.py                     # Git worktree utilities for parallel isolation
    discovery/
        __init__.py
        analyzer.py                 # Static project analyzer
        analyzer_helpers.py         # Analyzer helper functions
        generator.py                # Artifact generator (config.yaml, CLAUDE.md, etc.)
        interview.py                # Interactive discovery interview
        templates/                  # Templates for generated docs
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
        structural_check.py         # File size and sensitive file checks
    status/
        __init__.py
        parser.py                   # STATUS.md parser
        templates.py                # STATUS.md scaffold templates
        writer.py                   # STATUS.md writer
    web/
        __init__.py
        server.py                   # Web dashboard (SSE + interactive controls)
tests/                              # Test suite (67 files, 1074 tests)
docs/                               # Usage guides and reference
```

## Reporting Issues

Open an issue on [GitHub](https://github.com/eddieran/lindy-orchestrator/issues) with:
- Steps to reproduce
- Expected vs actual behavior
- Python version and OS

## License

By contributing, you agree that your contributions will be licensed under the [MIT License](LICENSE).
