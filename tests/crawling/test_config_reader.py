"""Unit tests for config_reader module."""

from pathlib import Path
import pytest

from src.crawling.config_reader import (
    _format_arxiv_id,
    load_all_members,
    load_member_range,
)


def test_format_arxiv_id() -> None:
    """Test formatting month and integer ID into arXiv canonical ID."""
    assert _format_arxiv_id("2025-12", 3231) == "2512.03231"
    assert _format_arxiv_id("2026-03", 10580) == "2603.10580"
    assert _format_arxiv_id("2026-02", 5) == "2602.00005"


def test_load_member_range_24127559() -> None:
    """Test loading specific range for Nguyen Thanh Toan (24127559)."""
    info = load_member_range("24127559")
    assert info["student_id"] == "24127559"
    assert info["member_name"] == "Nguyen Thanh Toan"
    assert info["start_month"] == "2025-12"
    assert info["start_id"] == 3231
    assert info["end_id"] == 8230

    ids = info["arxiv_ids"]
    assert len(ids) == 5000
    assert ids[0] == "2512.03231"
    assert ids[-1] == "2512.08230"


def test_load_all_members() -> None:
    """Test that all 5 members defined in the course config are loaded."""
    members = load_all_members()
    assert len(members) == 5

    student_ids = [m["student_id"] for m in members]
    assert "24127559" in student_ids
    assert "24127456" in student_ids
    assert "24127390" in student_ids
    assert "24127101" in student_ids
    assert "24127590" in student_ids

    for m in members:
        assert len(m["arxiv_ids"]) == 5000


def test_invalid_student_id() -> None:
    """Test that an unknown student ID raises a ValueError."""
    with pytest.raises(ValueError, match="not found in configuration"):
        load_member_range("99999999")


def test_nonexistent_config_file() -> None:
    """Test FileNotFoundError when config file is missing."""
    with pytest.raises(FileNotFoundError):
        load_member_range("24127559", config_path=Path("nonexistent/path"))
