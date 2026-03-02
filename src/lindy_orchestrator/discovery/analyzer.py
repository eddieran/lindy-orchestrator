"""Static project analyzer — scans a project directory to build a ProjectProfile.

No LLM calls. Pure filesystem analysis: tech stacks, dir trees, commands, CI, docs.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from ..models import ModuleProfile, ProjectProfile

# ---------------------------------------------------------------------------
# Ignored directories for tree generation
# ---------------------------------------------------------------------------

_IGNORED_DIRS = {
    ".git",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    ".env",
    ".tox",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "dist",
    "build",
    ".eggs",
    "target",  # Rust/Java
    ".next",
    ".nuxt",
    ".output",
    "vendor",  # Go
    ".terraform",
    "coverage",
    ".coverage",
    "htmlcov",
}

# Marker files that identify a module directory
_MODULE_MARKERS: dict[str, str] = {
    "pyproject.toml": "Python",
    "setup.py": "Python",
    "setup.cfg": "Python",
    "requirements.txt": "Python",
    "package.json": "Node.js",
    "Cargo.toml": "Rust",
    "go.mod": "Go",
    "pom.xml": "Java/Maven",
    "build.gradle": "Java/Gradle",
    "build.gradle.kts": "Kotlin/Gradle",
    "CMakeLists.txt": "C/C++",
    "Makefile": "Make",
    "mix.exs": "Elixir",
    "Gemfile": "Ruby",
    "composer.json": "PHP",
    "pubspec.yaml": "Dart/Flutter",
    "Package.swift": "Swift",
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def analyze_project(root: Path, max_depth: int = 1) -> ProjectProfile:
    """Analyze a project directory and return a structured profile."""
    root = root.resolve()

    modules = _detect_modules(root, max_depth)
    for mod in modules:
        _enrich_module(root, mod)

    cross_files = _detect_cross_module_files(root)
    git_remote, default_branch = _detect_git_info(root)
    ci = _detect_ci(root)

    return ProjectProfile(
        name=root.name,
        root=str(root),
        modules=modules,
        cross_module_files=cross_files,
        git_remote=git_remote,
        default_branch=default_branch,
        detected_ci=ci,
        monorepo=len(modules) > 1,
    )


# ---------------------------------------------------------------------------
# Module detection
# ---------------------------------------------------------------------------


def _detect_modules(root: Path, max_depth: int) -> list[ModuleProfile]:
    """Detect project modules by scanning for marker files."""
    modules = []
    for item in sorted(root.iterdir()):
        if not item.is_dir() or item.name.startswith(".") or item.name in _IGNORED_DIRS:
            continue
        tech = _detect_tech(item, max_depth)
        if tech:
            modules.append(
                ModuleProfile(
                    name=item.name,
                    path=item.name,
                    tech_stack=[tech],
                )
            )
    # If no sub-modules found, treat root as single module
    if not modules:
        tech = _detect_tech(root, 0)
        if tech:
            modules.append(
                ModuleProfile(
                    name=root.name,
                    path=".",
                    tech_stack=[tech],
                )
            )
    return modules


def _detect_tech(path: Path, depth: int) -> str:
    """Detect primary technology in a directory."""
    for marker, tech in _MODULE_MARKERS.items():
        if (path / marker).exists():
            return tech
    if (path / "src").is_dir():
        return "source directory"
    if depth > 0:
        for sub in sorted(path.iterdir()):
            if sub.is_dir() and sub.name not in _IGNORED_DIRS and not sub.name.startswith("."):
                result = _detect_tech(sub, depth - 1)
                if result:
                    return result
    return ""


# ---------------------------------------------------------------------------
# Module enrichment
# ---------------------------------------------------------------------------


def _enrich_module(root: Path, mod: ModuleProfile) -> None:
    """Fill in a ModuleProfile with detailed analysis."""
    mod_path = root / mod.path

    # Dependencies + enhanced tech stack
    deps, extra_tech = _parse_dependencies(mod_path)
    mod.dependencies = deps
    mod.tech_stack = list(dict.fromkeys(mod.tech_stack + extra_tech))

    # Directory tree
    mod.dir_tree = _generate_tree(mod_path, max_depth=3, max_items=40)

    # Entry points
    mod.entry_points = _detect_entry_points(mod_path)

    # Commands (test, build, lint)
    test_cmds, build_cmds, lint_cmds = _detect_commands(mod_path)
    mod.test_commands = test_cmds
    mod.build_commands = build_cmds
    mod.lint_commands = lint_cmds

    # Existing docs
    mod.existing_docs = _read_existing_docs(mod_path)

    # Detected patterns
    mod.detected_patterns = _detect_patterns(mod_path, mod.dependencies)


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
# Directory tree
# ---------------------------------------------------------------------------


def _generate_tree(path: Path, max_depth: int = 3, max_items: int = 40, prefix: str = "") -> str:
    """Generate a directory tree string, similar to the `tree` command."""
    lines: list[str] = []
    if not prefix:
        lines.append(f"{path.name}/")

    if max_depth <= 0:
        return "\n".join(lines)

    try:
        entries = sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    except PermissionError:
        return "\n".join(lines)

    # Filter out ignored dirs and hidden files
    entries = [
        e
        for e in entries
        if not (e.is_dir() and e.name in _IGNORED_DIRS)
        and not (e.name.startswith(".") and e.name not in (".env.example",))
    ]

    count = 0
    for i, entry in enumerate(entries):
        if count >= max_items:
            lines.append(f"{prefix}... ({len(entries) - count} more)")
            break
        count += 1

        is_last = i == len(entries) - 1
        connector = "└── " if is_last else "├── "
        extension = "    " if is_last else "│   "

        if entry.is_dir():
            lines.append(f"{prefix}{connector}{entry.name}/")
            subtree = _generate_tree(
                entry,
                max_depth=max_depth - 1,
                max_items=max_items - count,
                prefix=prefix + extension,
            )
            # Only add subtree lines (skip the root line)
            sub_lines = subtree.splitlines()
            if sub_lines:
                lines.extend(sub_lines[1:] if sub_lines[0].endswith("/") else sub_lines)
        else:
            lines.append(f"{prefix}{connector}{entry.name}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------


def _detect_entry_points(path: Path) -> list[str]:
    """Detect likely entry points for a module."""
    candidates = [
        "src/main.py",
        "src/__main__.py",
        "main.py",
        "__main__.py",
        "app.py",
        "server.py",
        "index.ts",
        "index.js",
        "src/index.ts",
        "src/index.js",
        "src/main.ts",
        "src/main.rs",
        "main.go",
        "cmd/main.go",
    ]
    found = []
    for c in candidates:
        if (path / c).exists():
            found.append(c)
    return found


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


# ---------------------------------------------------------------------------
# Existing docs
# ---------------------------------------------------------------------------


def _read_existing_docs(path: Path) -> str:
    """Read existing documentation files (README, CLAUDE.md)."""
    docs = []
    for name in ("CLAUDE.md", "README.md"):
        fp = path / name
        if fp.exists():
            try:
                content = fp.read_text(encoding="utf-8")[:3000]
                docs.append(f"=== {name} ===\n{content}")
            except OSError:
                pass
    return "\n\n".join(docs)


# ---------------------------------------------------------------------------
# Pattern detection
# ---------------------------------------------------------------------------


def _detect_patterns(path: Path, deps: dict[str, str]) -> list[str]:
    """Detect high-level patterns from directory structure and dependencies."""
    patterns = []

    # REST API
    api_indicators = {"fastapi", "flask", "django", "express", "fastify", "@nestjs/core", "gin"}
    if api_indicators & set(deps.keys()):
        patterns.append("REST API")

    # Database
    db_indicators = {"sqlalchemy", "prisma", "typeorm", "diesel", "sqlx", "gorm"}
    if db_indicators & set(deps.keys()):
        patterns.append("database ORM")

    # Frontend SPA
    spa_indicators = {"react", "vue", "svelte", "next", "nuxt", "@angular/core"}
    if spa_indicators & set(deps.keys()):
        patterns.append("frontend SPA")

    # Async
    if "celery" in deps or "bull" in deps or "bullmq" in deps:
        patterns.append("async job queue")

    # Migrations
    if (path / "migrations").is_dir() or (path / "alembic").is_dir():
        patterns.append("database migrations")

    # Docker
    if (path / "Dockerfile").exists() or (path / "docker-compose.yml").exists():
        patterns.append("containerized")

    # Terraform / IaC
    if any(path.glob("*.tf")):
        patterns.append("infrastructure as code")

    return patterns


# ---------------------------------------------------------------------------
# Git info
# ---------------------------------------------------------------------------


def _detect_git_info(root: Path) -> tuple[str, str]:
    """Detect git remote URL and default branch."""
    remote = ""
    branch = "main"

    try:
        proc = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=5,
        )
        if proc.returncode == 0:
            remote = proc.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    try:
        proc = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=5,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            branch = proc.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    return remote, branch


# ---------------------------------------------------------------------------
# CI detection
# ---------------------------------------------------------------------------


def _detect_ci(root: Path) -> str:
    """Detect CI/CD configuration."""
    # GitHub Actions
    workflows_dir = root / ".github" / "workflows"
    if workflows_dir.is_dir():
        files = list(workflows_dir.glob("*.yml")) + list(workflows_dir.glob("*.yaml"))
        if files:
            names = [f.name for f in files]
            return f"GitHub Actions ({', '.join(names)})"

    # GitLab CI
    if (root / ".gitlab-ci.yml").exists():
        return "GitLab CI"

    # Jenkins
    if (root / "Jenkinsfile").exists():
        return "Jenkins"

    # CircleCI
    if (root / ".circleci" / "config.yml").exists():
        return "CircleCI"

    return ""


# ---------------------------------------------------------------------------
# Cross-module file detection
# ---------------------------------------------------------------------------


def _detect_cross_module_files(root: Path) -> list[str]:
    """Detect files at root level that are shared across modules."""
    cross_files = []
    interesting = {
        "CONTRACTS.md",
        "docker-compose.yml",
        "docker-compose.yaml",
        "Makefile",
        ".env.example",
        "CLAUDE.md",
        "README.md",
    }
    for name in interesting:
        if (root / name).exists():
            cross_files.append(name)
    return cross_files
