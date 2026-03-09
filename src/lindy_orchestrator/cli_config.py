"""Config management commands (global and project-local).

Usage:
  lindy-orchestrate config show                          # show global + local
  lindy-orchestrate config set provider codex_cli        # set globally
  lindy-orchestrate config set --local provider codex_cli  # set in ./orchestrator.yaml
"""

from __future__ import annotations

from pathlib import Path

import typer
import yaml
from rich.console import Console
from rich.table import Table

from .config import (
    CONFIG_FILENAME,
    GLOBAL_CONFIG_PATH,
    VALID_PROVIDERS,
    GlobalConfig,
    load_global_config,
    save_global_config,
)

SETTABLE_KEYS = {"provider"}


def _read_local_provider(cwd: Path) -> str | None:
    """Read dispatcher.provider from ./orchestrator.yaml, or None if absent."""
    cfg_file = cwd / CONFIG_FILENAME
    if not cfg_file.exists():
        return None
    try:
        raw = yaml.safe_load(cfg_file.read_text(encoding="utf-8")) or {}
        return raw.get("dispatcher", {}).get("provider")
    except Exception:
        return None


def _write_local_provider(cwd: Path, provider: str) -> None:
    """Set dispatcher.provider in ./orchestrator.yaml (atomic write).

    Creates a minimal orchestrator.yaml if it does not exist yet.
    """
    import os
    import tempfile

    cfg_file = cwd / CONFIG_FILENAME
    if cfg_file.exists():
        raw = yaml.safe_load(cfg_file.read_text(encoding="utf-8")) or {}
    else:
        raw = {}

    raw.setdefault("dispatcher", {})["provider"] = provider

    fd, tmp = tempfile.mkstemp(dir=cwd, suffix=".tmp", prefix="orchestrator_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            yaml.dump(raw, f, default_flow_style=False, allow_unicode=True)
        os.replace(tmp, cfg_file)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def register_config_commands(app: typer.Typer, console: Console) -> None:
    """Register the `config` command group on the Typer app."""

    config_app = typer.Typer(name="config", help="Manage lindy-orchestrate settings.")
    app.add_typer(config_app, name="config")

    @config_app.command("show")
    def config_show() -> None:
        """Show current configuration (global and project-local)."""
        cwd = Path.cwd()
        global_cfg = load_global_config()
        local_provider = _read_local_provider(cwd)

        table = Table(title="lindy-orchestrate config", show_header=True)
        table.add_column("Scope", style="bold cyan")
        table.add_column("Key")
        table.add_column("Value", style="bold")
        table.add_column("Source", style="dim")

        global_source = str(GLOBAL_CONFIG_PATH) if GLOBAL_CONFIG_PATH.exists() else "default"
        table.add_row("global", "provider", global_cfg.provider, global_source)

        if local_provider:
            local_source = str(cwd / CONFIG_FILENAME)
            table.add_row("local", "provider", local_provider, local_source)
        else:
            table.add_row("local", "provider", "(not set)", str(cwd / CONFIG_FILENAME))

        console.print()
        console.print(table)
        console.print()

        # Show effective provider (local > global)
        effective = local_provider or global_cfg.provider
        console.print(f"Effective provider: [bold green]{effective}[/]")
        console.print(f"Valid providers:    [dim]{', '.join(sorted(VALID_PROVIDERS))}[/dim]")
        console.print()
        console.print("To set globally:  [bold]lindy-orchestrate config set provider <name>[/bold]")
        console.print(
            "To set locally:   [bold]lindy-orchestrate config set --local provider <name>[/bold]"
        )

    @config_app.command("set")
    def config_set(
        key: str = typer.Argument(..., help="Setting name (e.g. provider)"),
        value: str = typer.Argument(..., help="New value"),
        local: bool = typer.Option(
            False,
            "--local",
            help="Write to ./orchestrator.yaml instead of ~/.lindy/config.yaml",
        ),
    ) -> None:
        """Set a configuration value (global by default, use --local for project scope).

        Examples:

          lindy-orchestrate config set provider codex_cli
          lindy-orchestrate config set --local provider claude_cli
        """
        if key not in SETTABLE_KEYS:
            console.print(f"[red]Unknown key: {key!r}[/]")
            console.print(f"Valid keys: {', '.join(sorted(SETTABLE_KEYS))}")
            raise typer.Exit(1)

        if key == "provider":
            if value not in VALID_PROVIDERS:
                console.print(f"[red]Invalid provider: {value!r}[/]")
                console.print(f"Valid options: {', '.join(sorted(VALID_PROVIDERS))}")
                raise typer.Exit(1)

            if local:
                cwd = Path.cwd()
                _write_local_provider(cwd, value)
                dest = cwd / CONFIG_FILENAME
                console.print(f"[green]✓[/] provider = [bold]{value}[/]  (saved to {dest})")
            else:
                cfg = GlobalConfig(provider=value)
                save_global_config(cfg)
                console.print(
                    f"[green]✓[/] provider = [bold]{value}[/]  (saved to {GLOBAL_CONFIG_PATH})"
                )
