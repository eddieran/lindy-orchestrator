"""Tests for STATUS.md parser."""

from pathlib import Path

from lindy_orchestrator.status.parser import parse_status_md


FIXTURES = Path(__file__).parent / "fixtures"


def test_parse_sample_status():
    status = parse_status_md(FIXTURES / "sample_status.md")

    assert status.meta.module == "backend"
    assert status.meta.overall_health == "GREEN"
    assert status.meta.last_updated == "2026-03-01 10:00 UTC"

    assert len(status.active_work) == 2
    assert status.active_work[0].id == "B-001"
    assert status.active_work[0].task == "Add user auth"
    assert status.active_work[0].status == "IN_PROGRESS"
    assert status.active_work[1].id == "B-002"
    assert status.active_work[1].blocked_by == "B-001"

    assert len(status.completed) == 1
    assert status.completed[0].id == "B-000"

    assert status.key_metrics["test_count"] == "142"
    assert status.key_metrics["coverage"] == "87%"

    assert len(status.blockers) == 0


def test_parse_nonexistent_file(tmp_path):
    status = parse_status_md(tmp_path / "does_not_exist.md")
    assert status.meta.module == ""
    assert status.active_work == []


def test_parse_empty_file(tmp_path):
    f = tmp_path / "STATUS.md"
    f.write_text("")
    status = parse_status_md(f)
    assert status.meta.module == ""
    assert status.active_work == []


def test_parse_minimal_status(tmp_path):
    f = tmp_path / "STATUS.md"
    f.write_text("""\
# Test Status

## Meta
| Key | Value |
|-----|-------|
| module | test |
| overall_health | YELLOW |

## Blockers
- API key expired
- Database migration pending
""")
    status = parse_status_md(f)
    assert status.meta.module == "test"
    assert status.meta.overall_health == "YELLOW"
    assert len(status.blockers) == 2
    assert "API key expired" in status.blockers
