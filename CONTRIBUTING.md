# Contributing to lindy-orchestrator

## Development Setup

```bash
git clone https://github.com/eddieran/lindy-orchestrator.git
cd lindy-orchestrator
pip install -e ".[dev]"
```

Requires Python 3.11+.

## Running Tests

```bash
pytest tests/ -v
```

All tests must pass before submitting a PR. CI runs tests on Python 3.11, 3.12, and 3.13.

## Code Style

This project uses [Ruff](https://docs.astral.sh/ruff/) for linting and formatting:

```bash
ruff check src/ tests/
ruff format src/ tests/
```

Configuration is in `pyproject.toml`:
- Target: Python 3.11
- Line length: 100

## Pull Request Process

1. Fork the repo and create a feature branch from `main`.
2. Make your changes. Add tests for new functionality.
3. Run `pytest tests/ -v` and `ruff check src/ tests/` locally.
4. Submit a PR against `main` with a clear description of the change.

## Project Structure

```
src/lindy_orchestrator/     # Core library
tests/                      # Test suite (pytest)
docs/                       # Usage guides
```

Key modules:
- `cli.py` — CLI entry point (Typer)
- `cli_ext.py` — Extension commands (gc, scan, issues, run-issue, mailbox)
- `config.py` — Configuration loading (Pydantic)
- `dispatcher.py` — Claude Code CLI subprocess management
- `scheduler.py` — DAG-based parallel task execution
- `planner.py` — Goal decomposition via LLM
- `qa/` — Pluggable QA gate system (structural, layer, CI, command, agent checks)
- `entropy/` — Architecture drift and quality decay scanning
- `trackers/` — Issue tracker integration (GitHub, Linear)
- `mailbox.py` — Inter-agent messaging system

## Reporting Issues

Open an issue on [GitHub](https://github.com/eddieran/lindy-orchestrator/issues) with:
- Steps to reproduce
- Expected vs actual behavior
- Python version and OS

## License

By contributing, you agree that your contributions will be licensed under the [MIT License](LICENSE).
