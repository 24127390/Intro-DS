"""Unit tests for entry_discovery module."""

from datetime import datetime, timezone
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.crawling.entry_discovery import (
    _parse_version_info,
    discover_papers,
    serialize_arxiv_result,
)


def test_parse_version_info() -> None:
    """Test extracting list of versions from short arXiv IDs."""
    v4_list = _parse_version_info("2512.03231v4", "2512.03231")
    assert v4_list == [
        "2512.03231v1",
        "2512.03231v2",
        "2512.03231v3",
        "2512.03231v4",
    ]

    v1_list = _parse_version_info("2512.03231v1", "2512.03231")
    assert v1_list == ["2512.03231v1"]

    # When no version suffix is provided, default to v1
    v_default = _parse_version_info("2512.03231", "2512.03231")
    assert v_default == ["2512.03231v1"]


def test_serialize_arxiv_result() -> None:
    """Test serializing an arxiv.Result object into standard metadata dict."""
    mock_result = MagicMock()
    mock_result.get_short_id.return_value = "2512.03231v2"
    mock_result.title = "Sample Deep Learning Paper"

    auth1 = MagicMock()
    auth1.name = "Alice Nguyen"
    auth2 = MagicMock()
    auth2.name = "Bob Tran"
    mock_result.authors = [auth1, auth2]

    mock_result.published = datetime(2025, 12, 1, 10, 0, 0, tzinfo=timezone.utc)
    mock_result.updated = datetime(2025, 12, 5, 12, 0, 0, tzinfo=timezone.utc)
    mock_result.summary = "This is a test abstract."
    mock_result.journal_ref = "Nature ML 2026"
    mock_result.primary_category = "cs.LG"
    mock_result.categories = ["cs.LG", "stat.ML"]
    mock_result.doi = "10.1000/182"

    data = serialize_arxiv_result(mock_result, "2512.03231")

    assert data["arxiv_id"] == "2512.03231"
    assert data["title"] == "Sample Deep Learning Paper"
    assert data["authors"] == ["Alice Nguyen", "Bob Tran"]
    assert data["submission_date"] == "2025-12-01T10:00:00+00:00"
    assert data["revised_dates"] == ["2025-12-05T12:00:00+00:00"]
    assert data["abstract"] == "This is a test abstract."
    assert data["venue"] == "Nature ML 2026"
    assert data["versions"] == ["2512.03231v1", "2512.03231v2"]


def test_discover_papers_mocked(tmp_path: Path) -> None:
    """Test discover_papers caching and file creation with mocked arxiv client."""
    mock_result = MagicMock()
    mock_result.get_short_id.return_value = "2512.03231v1"
    mock_result.title = "Paper 1"
    mock_result.authors = []
    mock_result.published = datetime(2025, 12, 1, tzinfo=timezone.utc)
    mock_result.updated = datetime(2025, 12, 1, tzinfo=timezone.utc)
    mock_result.summary = "Abstract 1"
    mock_result.journal_ref = None
    mock_result.primary_category = "cs.CV"
    mock_result.categories = ["cs.CV"]
    mock_result.doi = None

    with patch("src.crawling.entry_discovery.arxiv.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.results.return_value = [mock_result]
        mock_client_cls.return_value = mock_client

        res = discover_papers(
            ["2512.03231"],
            output_dir=tmp_path,
            batch_size=10,
            delay=0.0,
        )

        assert res["total"] == 1
        assert res["resolved"] == 1
        assert res["cached"] == 0

        meta_file = tmp_path / "2512.03231" / "metadata.json"
        assert meta_file.exists()

        with meta_file.open("r", encoding="utf-8") as f:
            saved = json.load(f)
        assert saved["arxiv_id"] == "2512.03231"
        assert saved["title"] == "Paper 1"

        # Second call should use cache
        res_cached = discover_papers(
            ["2512.03231"],
            output_dir=tmp_path,
            batch_size=10,
            delay=0.0,
        )
        assert res_cached["cached"] == 1
        assert res_cached["resolved"] == 0
