"""Tests for status/writer.py — update_meta_timestamp and update_root_status."""

from __future__ import annotations

import re

import pytest

from lindy_orchestrator.status.writer import update_meta_timestamp, update_root_status


class TestUpdateMetaTimestamp:
    def _make_status(self, tmp_path, content=None):
        path = tmp_path / "STATUS.md"
        if content is None:
            content = (
                "# Module Status\n\n"
                "## Meta\n"
                "| Key | Value |\n"
                "|-----|-------|\n"
                "| module | test |\n"
                "| last_updated | 2025-01-01 00:00 UTC |\n"
                "| overall_health | GREEN |\n"
            )
        path.write_text(content, encoding="utf-8")
        return path

    def test_updates_timestamp(self, tmp_path):
        path = self._make_status(tmp_path)
        update_meta_timestamp(path)

        text = path.read_text(encoding="utf-8")
        # Should no longer contain old timestamp
        assert "2025-01-01" not in text
        # Should contain a new timestamp in YYYY-MM-DD HH:MM UTC format
        assert re.search(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2} UTC", text)

    def test_preserves_other_content(self, tmp_path):
        path = self._make_status(tmp_path)
        update_meta_timestamp(path)

        text = path.read_text(encoding="utf-8")
        assert "| module | test |" in text
        assert "| overall_health | GREEN |" in text

    def test_only_replaces_first_occurrence(self, tmp_path):
        content = (
            "## Meta\n"
            "| last_updated | 2025-01-01 |\n"
            "## Notes\n"
            "| last_updated | keep_this |\n"
        )
        path = self._make_status(tmp_path, content)
        update_meta_timestamp(path)

        text = path.read_text(encoding="utf-8")
        assert "keep_this" in text


class TestUpdateRootStatus:
    def test_valid_content(self, tmp_path):
        path = tmp_path / "STATUS.md"
        path.write_text("old content")

        update_root_status(path, "# Root Status\n\nAll good.")
        assert path.read_text() == "# Root Status\n\nAll good."

    def test_invalid_content_raises(self, tmp_path):
        path = tmp_path / "STATUS.md"
        path.write_text("old content")

        with pytest.raises(ValueError, match="does not look like"):
            update_root_status(path, "This is not a status file")

    def test_empty_content_raises(self, tmp_path):
        path = tmp_path / "STATUS.md"
        path.write_text("old content")

        with pytest.raises(ValueError, match="does not look like"):
            update_root_status(path, "   ")

    def test_content_with_leading_whitespace_and_hash(self, tmp_path):
        path = tmp_path / "STATUS.md"
        path.write_text("old")

        # Content with leading whitespace before # should fail
        with pytest.raises(ValueError):
            update_root_status(path, "   not starting with hash")
