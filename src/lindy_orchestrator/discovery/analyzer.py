"""Static project analyzer — scans a project directory to build a ProjectProfile.

No LLM calls. Pure filesystem analysis: tech stacks, dir trees, commands, CI, docs.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from ..models import ModuleProfile, ProjectProfile
from .analyzer_helpers import _detect_commands, _parse_dependencies

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
    mod.existing_docs = _read_existing_docs(mod_path, root=root, module_name=mod.name)

    # Detected patterns
    mod.detected_patterns = _detect_patterns(mod_path, mod.dependencies)


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
# Existing docs
# ---------------------------------------------------------------------------


def _read_existing_docs(
    path: Path, root: Path | None = None, module_name: str = ""
) -> str:
    """Read existing documentation files (README, .orchestrator/claude/)."""
    docs = []

    # Read CLAUDE docs from .orchestrator/claude/ directory
    if root is not None:
        claude_dir = root / ".orchestrator" / "claude"
        for name in ("root.md", f"{module_name}.md"):
            fp = claude_dir / name
            if fp.exists():
                try:
                    content = fp.read_text(encoding="utf-8")[:3000]
                    docs.append(f"=== .orchestrator/claude/{name} ===\n{content}")
                except OSError:
                    pass

    # Keep README.md reading from module path
    readme = path / "README.md"
    if readme.exists():
        try:
            content = readme.read_text(encoding="utf-8")[:3000]
            docs.append(f"=== README.md ===\n{content}")
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
