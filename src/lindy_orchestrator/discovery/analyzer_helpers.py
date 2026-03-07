"""Helper functions for the static project analyzer.

Contains dependency parsing and command detection logic extracted from analyzer.py.
"""

from __future__ import annotations

import json
import re
from pathlib import Path


# ---------------------------------------------------------------------------
# Dependency parsing
# ---------------------------------------------------------------------------


def _parse_dependencies(path: Path) -> tuple[dict[str, str], list[str]]:
    """Parse dependencies from manifest files. Returns (deps, extra_tech_stack)."""
    deps: dict[str, str] = {}
    extra_tech: list[str] = []

    # Python: pyproject.toml
    pyproject = path / "pyproject.toml"
    if pyproject.exists():
        deps.update(_parse_pyproject_deps(pyproject))
        extra_tech.extend(_infer_python_tech(deps))

    # Node.js: package.json
    pkg_json = path / "package.json"
    if pkg_json.exists():
        deps.update(_parse_package_json_deps(pkg_json))
        extra_tech.extend(_infer_node_tech(deps))

    # Rust: Cargo.toml
    cargo = path / "Cargo.toml"
    if cargo.exists():
        deps.update(_parse_cargo_deps(cargo))

    # Go: go.mod
    gomod = path / "go.mod"
    if gomod.exists():
        deps.update(_parse_gomod_deps(gomod))

    return deps, extra_tech


def _parse_pyproject_deps(path: Path) -> dict[str, str]:
    """Extract dependencies from pyproject.toml (best-effort, no toml dep)."""
    deps: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return deps

    in_deps = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped in ("[project]", "[tool.poetry.dependencies]"):
            continue
        if stripped == "dependencies = [":
            in_deps = True
            continue
        if in_deps:
            if stripped == "]":
                in_deps = False
                continue
            # Parse "fastapi>=0.100" style
            m = re.match(r'["\']([a-zA-Z0-9_-]+)([^"\']*)["\']', stripped)
            if m:
                deps[m.group(1).lower()] = m.group(2).strip().rstrip(",")
    return deps


def _parse_package_json_deps(path: Path) -> dict[str, str]:
    """Extract dependencies from package.json."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    deps: dict[str, str] = {}
    for section in ("dependencies", "devDependencies"):
        if section in data and isinstance(data[section], dict):
            deps.update(data[section])
    return deps


def _parse_cargo_deps(path: Path) -> dict[str, str]:
    """Extract dependencies from Cargo.toml (best-effort)."""
    deps: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return deps

    in_deps = False
    for line in text.splitlines():
        if line.strip() == "[dependencies]":
            in_deps = True
            continue
        if in_deps:
            if line.strip().startswith("["):
                break
            m = re.match(r'(\w[\w-]*)\s*=\s*"([^"]*)"', line.strip())
            if m:
                deps[m.group(1)] = m.group(2)
    return deps


def _parse_gomod_deps(path: Path) -> dict[str, str]:
    """Extract dependencies from go.mod."""
    deps: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return deps

    in_require = False
    for line in text.splitlines():
        if line.strip() == "require (":
            in_require = True
            continue
        if in_require:
            if line.strip() == ")":
                break
            parts = line.strip().split()
            if len(parts) >= 2:
                deps[parts[0]] = parts[1]
    return deps


def _infer_python_tech(deps: dict[str, str]) -> list[str]:
    """Infer higher-level tech from Python dependencies."""
    tech = []
    known = {
        "fastapi": "FastAPI",
        "flask": "Flask",
        "django": "Django",
        "sqlalchemy": "SQLAlchemy",
        "pydantic": "Pydantic",
        "celery": "Celery",
        "redis": "Redis",
        "psycopg2": "PostgreSQL",
        "psycopg": "PostgreSQL",
        "asyncpg": "PostgreSQL",
        "pymongo": "MongoDB",
        "boto3": "AWS SDK",
        "torch": "PyTorch",
        "tensorflow": "TensorFlow",
        "numpy": "NumPy",
        "pandas": "Pandas",
    }
    for dep, label in known.items():
        if dep in deps:
            tech.append(label)
    return tech


def _infer_node_tech(deps: dict[str, str]) -> list[str]:
    """Infer higher-level tech from Node.js dependencies."""
    tech = []
    known = {
        "react": "React",
        "next": "Next.js",
        "vue": "Vue.js",
        "nuxt": "Nuxt.js",
        "svelte": "Svelte",
        "express": "Express",
        "fastify": "Fastify",
        "nestjs": "NestJS",
        "@nestjs/core": "NestJS",
        "prisma": "Prisma",
        "typeorm": "TypeORM",
        "tailwindcss": "Tailwind CSS",
        "typescript": "TypeScript",
    }
    for dep, label in known.items():
        if dep in deps:
            tech.append(label)
    return tech


# ---------------------------------------------------------------------------
# Command detection
# ---------------------------------------------------------------------------


def _detect_commands(path: Path) -> tuple[list[str], list[str], list[str]]:
    """Detect test, build, and lint commands. Returns (test, build, lint)."""
    test_cmds: list[str] = []
    build_cmds: list[str] = []
    lint_cmds: list[str] = []

    # Python
    has_python = (
        (path / "pyproject.toml").exists()
        or (path / "setup.py").exists()
        or (path / "requirements.txt").exists()
    )
    if has_python:
        if _has_pytest_config(path):
            test_cmds.append("pytest")
        if (path / "pyproject.toml").exists() or (path / "setup.py").exists():
            build_cmds.append("pip install -e .")
        else:
            build_cmds.append("pip install -r requirements.txt")
        if _file_mentions(path / "pyproject.toml", "ruff"):
            lint_cmds.append("ruff check .")
        if _file_mentions(path / "pyproject.toml", "mypy"):
            lint_cmds.append("mypy .")

    # Node.js
    pkg_json = path / "package.json"
    if pkg_json.exists():
        scripts = _parse_npm_scripts(pkg_json)
        if "test" in scripts:
            test_cmds.append(f"npm test  # {scripts['test']}")
        if "build" in scripts:
            build_cmds.append(f"npm run build  # {scripts['build']}")
        if "lint" in scripts:
            lint_cmds.append(f"npm run lint  # {scripts['lint']}")

    # Rust
    if (path / "Cargo.toml").exists():
        test_cmds.append("cargo test")
        build_cmds.append("cargo build")
        lint_cmds.append("cargo clippy")

    # Go
    if (path / "go.mod").exists():
        test_cmds.append("go test ./...")
        build_cmds.append("go build ./...")
        lint_cmds.append("go vet ./...")

    # Playwright
    if (path / "playwright.config.ts").exists() or (path / "playwright.config.js").exists():
        test_cmds.append("npx playwright test")

    # Makefile targets
    makefile = path / "Makefile"
    if makefile.exists():
        targets = _parse_makefile_targets(makefile)
        if "test" in targets and not test_cmds:
            test_cmds.append("make test")
        if "build" in targets and not build_cmds:
            build_cmds.append("make build")
        if "lint" in targets and not lint_cmds:
            lint_cmds.append("make lint")

    return test_cmds, build_cmds, lint_cmds


def _has_pytest_config(path: Path) -> bool:
    """Check if pytest is configured."""
    if (path / "pytest.ini").exists() or (path / "conftest.py").exists():
        return True
    if (path / "tests").is_dir():
        return True
    if _file_mentions(path / "pyproject.toml", "[tool.pytest"):
        return True
    if _file_mentions(path / "requirements.txt", "pytest"):
        return True
    return False


def _parse_npm_scripts(path: Path) -> dict[str, str]:
    """Extract scripts from package.json."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get("scripts", {})
    except (OSError, json.JSONDecodeError):
        return {}


def _parse_makefile_targets(path: Path) -> set[str]:
    """Extract target names from a Makefile."""
    targets: set[str] = set()
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            m = re.match(r"^([a-zA-Z_][\w-]*)\s*:", line)
            if m:
                targets.add(m.group(1))
    except OSError:
        pass
    return targets


def _file_mentions(path: Path, term: str) -> bool:
    """Check if a file exists and contains a term."""
    try:
        return term in path.read_text(encoding="utf-8")
    except OSError:
        return False
