"""Configuration system for lindy-orchestrator.

Loads orchestrator.yaml from the target project root.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Sub-models
# ---------------------------------------------------------------------------


class ModuleConfig(BaseModel):
    name: str
    path: str
    status_md: str = "STATUS.md"
    claude_md: str = "CLAUDE.md"
    repo: str = ""
    ci_workflow: str = "ci.yml"
    role: str = ""  # "qa" marks a module as QA dispatcher target


class PlannerConfig(BaseModel):
    mode: str = "cli"  # "cli" or "api"
    model: str = "claude-sonnet-4-20250514"
    max_tokens: int = 4096
    timeout_seconds: int = 120
    prompt_template: str | None = None  # Path to custom Jinja2 template


class StallEscalationConfig(BaseModel):
    warn_after_seconds: int = 300  # emit warning event after 5 min
    kill_after_seconds: int = 600  # kill process after 10 min


class DispatcherConfig(BaseModel):
    provider: str = "claude_cli"
    timeout_seconds: int = 1800
    stall_timeout_seconds: int = 600  # kept for backward compat
    stall_escalation: StallEscalationConfig = Field(default_factory=StallEscalationConfig)
    permission_mode: str = "bypassPermissions"
    max_output_chars: int = 50_000


class CICheckConfig(BaseModel):
    timeout_seconds: int = 900
    poll_interval: int = 30


class CustomGateConfig(BaseModel):
    name: str
    command: str
    cwd: str = "{module_path}"
    timeout: int = 600
    modules: list[str] = Field(default_factory=list)  # empty = all modules


class StructuralCheckConfig(BaseModel):
    max_file_lines: int = 500
    enforce_module_boundary: bool = True
    sensitive_patterns: list[str] = Field(default_factory=lambda: [".env", "*.key", "*.pem"])


class LayerCheckConfig(BaseModel):
    enabled: bool = True
    unknown_file_policy: str = "skip"  # skip | warn


class QAGatesConfig(BaseModel):
    ci_check: CICheckConfig = Field(default_factory=CICheckConfig)
    structural: StructuralCheckConfig = Field(default_factory=StructuralCheckConfig)
    layer_check: LayerCheckConfig = Field(default_factory=LayerCheckConfig)
    custom: list[CustomGateConfig] = Field(default_factory=list)


class SafetyConfig(BaseModel):
    dry_run: bool = False
    max_retries_per_task: int = 2
    max_parallel: int = 3


class MailboxConfig(BaseModel):
    enabled: bool = False  # opt-in
    dir: str = ".orchestrator/mailbox"
    inject_on_dispatch: bool = True  # auto-inject pending messages into prompts


class TrackerConfig(BaseModel):
    enabled: bool = False
    provider: str = "github"  # github | linear
    repo: str = ""
    labels: list[str] = Field(default_factory=lambda: ["orchestrator"])
    sync_on_complete: bool = True  # auto-comment + close on completion


class LoggingConfig(BaseModel):
    dir: str = ".orchestrator/logs"
    session_dir: str = ".orchestrator/sessions"
    log_file: str = "actions.jsonl"


class ProjectConfig(BaseModel):
    name: str = "project"
    branch_prefix: str = "af"


# ---------------------------------------------------------------------------
# Root config
# ---------------------------------------------------------------------------


class OrchestratorConfig(BaseModel):
    project: ProjectConfig = Field(default_factory=ProjectConfig)
    modules: list[ModuleConfig] = Field(default_factory=list)
    planner: PlannerConfig = Field(default_factory=PlannerConfig)
    dispatcher: DispatcherConfig = Field(default_factory=DispatcherConfig)
    qa_gates: QAGatesConfig = Field(default_factory=QAGatesConfig)
    safety: SafetyConfig = Field(default_factory=SafetyConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    mailbox: MailboxConfig = Field(default_factory=MailboxConfig)
    tracker: TrackerConfig = Field(default_factory=TrackerConfig)

    # Internal: set after loading, not from YAML
    _config_dir: Path = Path(".")

    model_config = {"arbitrary_types_allowed": True}

    @property
    def root(self) -> Path:
        """Project root — the directory containing orchestrator.yaml."""
        return self._config_dir

    def get_module(self, name: str) -> ModuleConfig:
        # "root" or "*" is a virtual fullstack module (project root)
        if name in ("root", "*"):
            return ModuleConfig(name="root", path=".")
        for m in self.modules:
            if m.name == name:
                return m
        raise ValueError(f"Unknown module: {name!r}. Available: {[m.name for m in self.modules]}")

    def module_path(self, name: str) -> Path:
        # "root" or "*" means the project root (fullstack tasks)
        if name in ("root", "*"):
            return self._config_dir.resolve()
        mod = self.get_module(name)
        return (self._config_dir / mod.path).resolve()

    def status_path(self, name: str) -> Path:
        mod = self.get_module(name)
        return (self._config_dir / mod.path / mod.status_md).resolve()

    def qa_module(self) -> ModuleConfig | None:
        """Return the module marked with role=qa, if any."""
        for m in self.modules:
            if m.role == "qa":
                return m
        return None

    @property
    def log_path(self) -> Path:
        return self._config_dir / self.logging.dir / self.logging.log_file

    @property
    def sessions_path(self) -> Path:
        return self._config_dir / self.logging.session_dir


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


CONFIG_FILENAME = "orchestrator.yaml"


def find_config(start: Path | None = None) -> Path | None:
    """Walk up from start to find orchestrator.yaml."""
    candidate = (start or Path.cwd()).resolve()
    for _ in range(10):
        cfg = candidate / CONFIG_FILENAME
        if cfg.exists():
            return cfg
        parent = candidate.parent
        if parent == candidate:
            break
        candidate = parent
    return None


def load_config(config_path: Path | str | None = None) -> OrchestratorConfig:
    """Load configuration from YAML file."""
    if config_path is None:
        found = find_config()
        if found is None:
            raise FileNotFoundError(
                f"No {CONFIG_FILENAME} found. Run `lindy-orchestrate init` first."
            )
        config_path = found

    path = Path(config_path).resolve()
    raw = _load_yaml(path)
    cfg = OrchestratorConfig.model_validate(raw)
    cfg._config_dir = path.parent
    return cfg


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data if isinstance(data, dict) else {}
