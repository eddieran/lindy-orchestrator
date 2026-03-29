"""Role-specific configuration models for lindy-orchestrator.

These are the standalone config classes for planner, generator, evaluator,
dispatcher, and QA gates.  They are re-exported from ``config.py`` so that
existing imports continue to work.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from .models import RoleProviderConfig


# ---------------------------------------------------------------------------
# Planner
# ---------------------------------------------------------------------------


class PlannerConfig(BaseModel):
    provider: str = "claude_cli"
    mode: str = "cli"  # "cli" or "api"
    model: str = "claude-sonnet-4-20250514"
    max_tokens: int = 4096
    timeout_seconds: int = 300  # planning complex goals can take 2-5 min
    prompt: str = ""
    prompt_template: str | None = None  # Path to custom Jinja2 template

    def to_role_provider_config(self) -> RoleProviderConfig:
        return RoleProviderConfig(provider=self.provider, timeout_seconds=self.timeout_seconds)


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------


class StallEscalationConfig(BaseModel):
    warn_after_seconds: int = 150  # emit warning event after 2.5 min
    kill_after_seconds: int = 600  # kill process after 10 min (reasoning can be slow)


class DispatcherConfig(BaseModel):
    provider: str = "claude_cli"
    timeout_seconds: int = 1800
    stall_timeout_seconds: int = 600  # kept for backward compat
    stall_escalation: StallEscalationConfig = Field(default_factory=StallEscalationConfig)
    permission_mode: str = "bypassPermissions"
    max_output_chars: int = 50_000
    prompt_template: str = ""

    def to_role_provider_config(self) -> RoleProviderConfig:
        return RoleProviderConfig(provider=self.provider, timeout_seconds=self.timeout_seconds)


# ---------------------------------------------------------------------------
# Generator
# ---------------------------------------------------------------------------


class GeneratorConfig(BaseModel):
    provider: str = "claude_cli"
    timeout_seconds: int = 1800
    stall_timeout: int = 600
    permission_mode: str = "bypassPermissions"
    max_output_chars: int = 200_000
    prompt_prefix: str = ""

    def to_role_provider_config(self) -> RoleProviderConfig:
        return RoleProviderConfig(provider=self.provider, timeout_seconds=self.timeout_seconds)


# ---------------------------------------------------------------------------
# Evaluator
# ---------------------------------------------------------------------------


class EvaluatorConfig(BaseModel):
    provider: str = "claude_cli"
    timeout_seconds: int = 300
    pass_threshold: int = 80
    prompt_prefix: str = ""

    def to_role_provider_config(self) -> RoleProviderConfig:
        return RoleProviderConfig(provider=self.provider, timeout_seconds=self.timeout_seconds)


# ---------------------------------------------------------------------------
# QA Gates
# ---------------------------------------------------------------------------


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


class LayerCheckConfig(BaseModel):
    # DEPRECATED: removed in v0.15
    enabled: bool = True
    unknown_file_policy: str = "skip"  # skip | warn


class QAGatesConfig(BaseModel):
    ci_check: CICheckConfig = Field(default_factory=CICheckConfig)
    structural: StructuralCheckConfig = Field(default_factory=StructuralCheckConfig)
    layer_check: LayerCheckConfig = Field(default_factory=LayerCheckConfig)
    custom: list[CustomGateConfig] = Field(default_factory=list)
