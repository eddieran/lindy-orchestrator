"""Tests for the version CLI command."""

from __future__ import annotations

import json

from typer.testing import CliRunner

from lindy_orchestrator import __version__
from lindy_orchestrator.cli import app

runner = CliRunner()


class TestVersionCommand:
    def test_version_output(self):
        result = runner.invoke(app, ["version"])
        assert result.exit_code == 0
        assert f"lindy-orchestrator v{__version__}" in result.output

    def test_version_json(self):
        result = runner.invoke(app, ["version", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data == {"version": __version__}

    def test_version_string_is_semver(self):
        import re

        assert re.match(r"^\d+\.\d+\.\d+", __version__), (
            f"Version {__version__!r} should be semver-like"
        )

    def test_version_json_has_single_key(self):
        result = runner.invoke(app, ["version", "--json"])
        data = json.loads(result.output)
        assert list(data.keys()) == ["version"]
