"""Configuration system for lindy-orchestrator.

Loads orchestrator.yaml from the target project root.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, PrivateAttr


# ---------------------------------------------------------------------------
# Sub-models
# ---------------------------------------------------------------------------


class ModuleConfig(BaseModel):
    name: str
    path: str
    status_md: str = "STATUS.md"
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
    warn_after_seconds: int = 150  # emit warning event after 2.5 min
    kill_after_seconds: int = 600  # kill process after 10 min (reasoning can be slow)


class DispatcherConfig(BaseModel):
    provider: str = "claude_cli"
    timeout_seconds: int = 1800
    stall_escalation: StallEscalationConfig = Field(default_factory=StallEscalationConfig)
    permission_mode: str = "bypassPermissions"
    max_output_chars: int = 50_000
    prompt_template: str = ""


class GeneratorConfig(BaseModel):
    provider: str = ""

    def model_post_init(self, __context: Any) -> None:
        if self.provider and self.provider not in VALID_PROVIDERS:
            raise ValueError(
                f"Invalid provider {self.provider!r}. Valid options: {sorted(VALID_PROVIDERS)}"
            )

    def resolved_provider(self, dispatcher_provider: str) -> str:
        return self.provider or dispatcher_provider


class EvaluatorConfig(DispatcherConfig):
    timeout_seconds: int = 300
    pass_threshold: int = 80
    prompt_prefix: str = ""


class CICheckConfig(BaseModel):
    timeout_seconds: int = 900
    poll_interval: int = 30


class CustomGateConfig(BaseModel):
    name: str
    command: str
    cwd: str = "{module_path}"
    timeout: int = 600
    modules: list[str] = Field(default_factory=list)  # empty = all modules
    required: bool = True  # False = failure is warning only, doesn't trigger retry
    diff_only: bool = False  # True = inject {changed_files} with git diff file list


class StructuralCheckConfig(BaseModel):
    max_file_lines: int = 500
    enforce_module_boundary: bool = True
    sensitive_patterns: list[str] = Field(default_factory=lambda: [".env", "*.key", "*.pem"])


class QAGatesConfig(BaseModel):
    ci_check: CICheckConfig = Field(default_factory=CICheckConfig)
    structural: StructuralCheckConfig = Field(default_factory=StructuralCheckConfig)
    custom: list[CustomGateConfig] = Field(default_factory=list)


class LifecycleHooksConfig(BaseModel):
    after_create: str = ""
    before_run: str = ""
    after_run: str = ""
    before_remove: str = ""
    timeout: int = 60


class SafetyConfig(BaseModel):
    dry_run: bool = False
    max_retries_per_task: int = 2
    max_parallel: int = 10
    module_concurrency: dict[str, int] = Field(default_factory=dict)


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
    generator: GeneratorConfig = Field(default_factory=GeneratorConfig)
    evaluator: EvaluatorConfig = Field(default_factory=EvaluatorConfig)
    qa_gates: QAGatesConfig = Field(default_factory=QAGatesConfig)
    safety: SafetyConfig = Field(default_factory=SafetyConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    lifecycle_hooks: LifecycleHooksConfig = Field(default_factory=LifecycleHooksConfig)

    # Internal: set after loading, not from YAML
    _config_dir: Path = PrivateAttr(default_factory=lambda: Path("."))
    _config_path: Path | None = PrivateAttr(default=None)
    _config_mtime: float = PrivateAttr(default=0.0)

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
        # Prefer new .orchestrator/ layout, fall back to legacy per-module path
        new_path = (self._config_dir / ORCH_DIR / "status" / f"{mod.name}.md").resolve()
        if new_path.exists():
            return new_path
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

    # -- .orchestrator/ scaffold paths --------------------------------------

    @property
    def orch_dir(self) -> Path:
        """Return the resolved path to the ``.orchestrator/`` directory."""
        return (self._config_dir / ORCH_DIR).resolve()

    @property
    def orch_config_path(self) -> Path:
        """Return the resolved path to ``.orchestrator/config.yaml``."""
        return (self._config_dir / NEW_CONFIG_FILENAME).resolve()

    def orch_status_path(self, name: str) -> Path:
        """Return the module status file under ``.orchestrator/status/``."""
        mod = self.get_module(name)
        return (self._config_dir / ORCH_DIR / "status" / f"{mod.name}.yaml").resolve()

    @property
    def orch_log_path(self) -> Path:
        """Return the resolved path to ``.orchestrator/logs/actions.jsonl``."""
        return (self._config_dir / ORCH_DIR / "logs" / self.logging.log_file).resolve()

    @property
    def orch_sessions_path(self) -> Path:
        """Return the resolved path to ``.orchestrator/sessions/``."""
        return (self._config_dir / ORCH_DIR / "sessions").resolve()

    @property
    def orch_mailbox_path(self) -> Path:
        """Return the resolved path to ``.orchestrator/mailbox/``."""
        return (self._config_dir / ORCH_DIR / "mailbox").resolve()

    def check_reload(self) -> OrchestratorConfig | None:
        """Check if config file changed and selectively reload safe sections.

        Returns the updated config if reloaded, None if unchanged.
        Only updates safety, dispatcher, qa_gates, and lifecycle_hooks —
        never modules (unsafe mid-run).
        """
        if self._config_path is None:
            return None
        try:
            mtime = self._config_path.stat().st_mtime
        except OSError:
            return None
        if mtime <= self._config_mtime:
            return None
        try:
            raw = _load_yaml(self._config_path)
            _normalize_qa_gates(raw)
            fresh = OrchestratorConfig.model_validate(raw)
        except Exception:
            return None
        self.safety = fresh.safety
        self.dispatcher = fresh.dispatcher
        self.qa_gates = fresh.qa_gates
        self.lifecycle_hooks = fresh.lifecycle_hooks
        self._config_mtime = mtime
        return self


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


CONFIG_FILENAME = "orchestrator.yaml"
ORCH_DIR = ".orchestrator"
NEW_CONFIG_FILENAME = f"{ORCH_DIR}/config.yaml"

# ---------------------------------------------------------------------------
# Global user config  (~/.lindy/config.yaml)
# ---------------------------------------------------------------------------

GLOBAL_CONFIG_DIR = Path.home() / ".lindy"
GLOBAL_CONFIG_PATH = GLOBAL_CONFIG_DIR / "config.yaml"

VALID_PROVIDERS = {"claude_cli", "codex_cli"}


class GlobalConfig(BaseModel):
    """User-level defaults that apply across all projects.

    Priority (highest → lowest):
      CLI --provider flag > orchestrator.yaml dispatcher.provider > GlobalConfig > built-in default
    """

    provider: str = "claude_cli"

    def model_post_init(self, __context: Any) -> None:
        if self.provider not in VALID_PROVIDERS:
            raise ValueError(
                f"Invalid provider {self.provider!r}. Valid options: {sorted(VALID_PROVIDERS)}"
            )


def load_global_config() -> GlobalConfig:
    """Load ~/.lindy/config.yaml; return defaults if missing."""
    if not GLOBAL_CONFIG_PATH.exists():
        return GlobalConfig()
    try:
        raw = yaml.safe_load(GLOBAL_CONFIG_PATH.read_text(encoding="utf-8")) or {}
        return GlobalConfig.model_validate(raw)
    except Exception:
        return GlobalConfig()


def save_global_config(cfg: GlobalConfig) -> None:
    """Atomically write ~/.lindy/config.yaml."""
    import os
    import tempfile

    GLOBAL_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    data = cfg.model_dump()
    fd, tmp = tempfile.mkstemp(dir=GLOBAL_CONFIG_DIR, suffix=".tmp", prefix="config_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            yaml.dump(data, f, default_flow_style=False, allow_unicode=True)
        os.replace(tmp, GLOBAL_CONFIG_PATH)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def find_config(start: Path | None = None) -> Path | None:
    """Walk up from *start* to find the orchestrator config file.

    Checks ``.orchestrator/config.yaml`` first, then falls back to the
    legacy ``orchestrator.yaml`` for backward compatibility.
    """
    candidate = (start or Path.cwd()).resolve()
    for _ in range(10):
        new_cfg = candidate / NEW_CONFIG_FILENAME
        if new_cfg.exists():
            return new_cfg
        legacy_cfg = candidate / CONFIG_FILENAME
        if legacy_cfg.exists():
            return legacy_cfg
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
    _normalize_qa_gates(raw)

    # Apply global config as defaults — only when project yaml doesn't explicitly set provider
    if "provider" not in raw.get("dispatcher", {}):
        global_cfg = load_global_config()
        raw.setdefault("dispatcher", {})["provider"] = global_cfg.provider

    cfg = OrchestratorConfig.model_validate(raw)
    # When loaded from .orchestrator/config.yaml, _config_dir must be the
    # project root (grandparent), not the .orchestrator/ directory.
    if path.parent.name == ORCH_DIR:
        cfg._config_dir = path.parent.parent
    else:
        cfg._config_dir = path.parent
    cfg._config_path = path
    try:
        cfg._config_mtime = path.stat().st_mtime
    except OSError:
        pass
    return cfg


_QA_GATES_KNOWN_KEYS = {"ci_check", "structural", "layer_check", "custom"}


def _normalize_qa_gates(raw: dict[str, Any]) -> None:
    """Convert module-scoped qa_gates into unified ``custom`` list.

    Users may write::

        qa_gates:
          backend:
            - name: pytest
              command: "cd backend && pytest"
          frontend:
            - name: playwright
              command: "npx playwright test"

    This normalizes them into::

        qa_gates:
          custom:
            - name: pytest
              command: "cd backend && pytest"
              modules: ["backend"]
              cwd: "."
            - name: playwright
              command: "npx playwright test"
              modules: ["frontend"]
              cwd: "."
    """
    qa = raw.get("qa_gates")
    if not isinstance(qa, dict):
        return

    custom: list[dict[str, Any]] = list(qa.get("custom", []))
    for key in list(qa.keys()):
        if key in _QA_GATES_KNOWN_KEYS:
            continue
        gates = qa.pop(key)
        if not isinstance(gates, list):
            continue
        for gate in gates:
            if not isinstance(gate, dict):
                continue
            gate.setdefault("modules", [key])
            # Module-scoped gates without explicit cwd run from project root
            # (commands like "cd backend && pytest" expect project root).
            gate.setdefault("cwd", ".")
            custom.append(gate)

    if custom:
        qa["custom"] = custom


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data if isinstance(data, dict) else {}
