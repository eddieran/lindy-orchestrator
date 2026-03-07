"""Tests for status/templates.py — generate_status_md."""

from __future__ import annotations

import re

from lindy_orchestrator.status.templates import generate_status_md


class TestGenerateStatusMd:
    def test_contains_module_name(self):
        result = generate_status_md("backend")
        assert "backend" in result

    def test_title_case_header(self):
        result = generate_status_md("frontend")
        assert result.startswith("# Frontend Status")

    def test_contains_meta_section(self):
        result = generate_status_md("test")
        assert "## Meta" in result
        assert "| module | test |" in result

    def test_contains_required_sections(self):
        result = generate_status_md("mod")
        sections = [
            "## Meta",
            "## Active Work",
            "## Completed (Recent)",
            "## Backlog",
            "## Cross-Module Requests",
            "## Cross-Module Deliverables",
            "## Key Metrics",
            "## Blockers",
        ]
        for section in sections:
            assert section in result, f"Missing section: {section}"

    def test_contains_timestamp(self):
        result = generate_status_md("mod")
        assert re.search(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2} UTC", result)

    def test_default_health_green(self):
        result = generate_status_md("mod")
        assert "| overall_health | GREEN |" in result

    def test_empty_backlog(self):
        result = generate_status_md("mod")
        assert "- (none)" in result

    def test_different_module_names(self):
        for name in ["api", "worker", "ml-pipeline"]:
            result = generate_status_md(name)
            assert f"| module | {name} |" in result
