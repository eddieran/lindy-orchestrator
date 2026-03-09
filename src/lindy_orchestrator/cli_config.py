"""Global config management commands.

Usage:
  lindy-orchestrate config show
  lindy-orchestrate config set provider codex_cli
"""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from .config import (
    GLOBAL_CONFIG_PATH,
    VALID_PROVIDERS,
    GlobalConfig,
    load_global_config,
    save_global_config,
)

SETTABLE_KEYS = {"provider"}


def register_config_commands(app: typer.Typer, console: Console) -> None:
    """Register the `config` command group on the Typer app."""

    config_app = typer.Typer(name="config", help="Manage global lindy-orchestrate settings.")
    app.add_typer(config_app, name="config")

    @config_app.command("show")
    def config_show() -> None:
        """Show current global configuration."""
        cfg = load_global_config()

        table = Table(title=f"Global config  [dim]{GLOBAL_CONFIG_PATH}[/dim]", show_header=True)
        table.add_column("Key", style="bold cyan")
        table.add_column("Value")
        table.add_column("Source", style="dim")

        source = "file" if GLOBAL_CONFIG_PATH.exists() else "default"
        table.add_row("provider", cfg.provider, source)

        console.print()
        console.print(table)
        console.print()
        console.print(f"Valid providers: [dim]{', '.join(sorted(VALID_PROVIDERS))}[/dim]")
        console.print("\nTo change: [bold]lindy-orchestrate config set provider <name>[/bold]")

    @config_app.command("set")
    def config_set(
        key: str = typer.Argument(..., help="Setting name (e.g. provider)"),
        value: str = typer.Argument(..., help="New value"),
    ) -> None:
        """Set a global configuration value.

        Example:

          lindy-orchestrate config set provider codex_cli
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
            cfg = load_global_config()
            cfg = GlobalConfig(provider=value)
            save_global_config(cfg)
            console.print(
                f"[green]✓[/] provider = [bold]{value}[/]  (saved to {GLOBAL_CONFIG_PATH})"
            )
