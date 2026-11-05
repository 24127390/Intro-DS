"""Unit tests for reference_crawler module."""

import io
import json
from pathlib import Path
from unittest.mock import MagicMock, patch
import urllib.error

from src.crawling.reference_crawler import (
    _parse_reference_item,
    crawl_references_for_corpus,
    fetch_paper_references,
)


def test_parse_reference_item_with_arxiv_id() -> None:
    """Test parsing a reference that contains an arXiv ID."""
    raw = {
        "paperId": "s2_abc123",
        "title": "Attention Is All You Need",
        "authors": [{"name": "Ashish Vaswani"}, {"name": "Noam Shazeer"}],
        "publicationDate": "2017-06-12",
        "externalIds": {"ArXiv": "1706.03762", "DOI": "10.5555/3295222.3295349"},
    }
    parsed = _parse_reference_item(raw)
    assert parsed["ref_s2_id"] == "s2_abc123"
    assert parsed["ref_arxiv_id"] == "1706.03762"
    assert parsed["ref_title"] == "Attention Is All You Need"
    assert parsed["ref_authors"] == ["Ashish Vaswani", "Noam Shazeer"]
    assert parsed["ref_submission_date"] == "2017-06-12"
    assert parsed["has_arxiv_id"] is True


def test_parse_reference_item_without_arxiv_id() -> None:
    """Test parsing a reference citing a book or non-arXiv paper."""
    raw = {
        "paperId": "s2_book999",
        "title": "Deep Learning Book",
        "authors": [{"name": "Ian Goodfellow"}],
        "publicationDate": "2016-11-18",
        "externalIds": {"ISBN": "9780262035613"},
    }
    parsed = _parse_reference_item(raw)
    assert parsed["ref_arxiv_id"] is None
    assert parsed["has_arxiv_id"] is False


def test_fetch_paper_references_mocked() -> None:
    """Test fetching and parsing references with mocked urllib."""
    mock_payload = {
        "paperId": "s2_paper_1",
        "references": [
            {
                "paperId": "s2_ref_1",
                "title": "Ref Paper 1",
                "authors": [{"name": "Author 1"}],
                "publicationDate": "2020-01-01",
                "externalIds": {"ArXiv": "2001.00001"},
            }
        ],
    }
    mock_resp = MagicMock()
    mock_resp.__enter__.return_value = mock_resp
    mock_resp.read.return_value = json.dumps(mock_payload).encode("utf-8")

    with patch("urllib.request.urlopen", return_value=mock_resp):
        res = fetch_paper_references("2512.03231", api_key="dummy_key")
        assert res is not None
        assert res["arxiv_id"] == "2512.03231"
        assert res["s2_paper_id"] == "s2_paper_1"
        assert len(res["references"]) == 1
        assert res["references"][0]["ref_arxiv_id"] == "2001.00001"


def test_fetch_paper_references_404_not_indexed() -> None:
    """Test handling of 404 response for newly submitted papers."""
    http_err = urllib.error.HTTPError(
        url="http://test",
        code=404,
        msg="Not Found",
        hdrs={},  # type: ignore
        fp=io.BytesIO(b""),
    )

    with patch("urllib.request.urlopen", side_effect=http_err):
        res = fetch_paper_references("2512.99999", api_key="dummy_key")
        assert res is not None
        assert res["references"] == []
        assert res.get("note") == "not_found_on_s2"


def test_crawl_references_for_corpus_checkpoint(tmp_path: Path) -> None:
    """Test that corpus crawling saves and loads from checkpoint file."""
    # Pre-populate one paper's refs
    p_dir = tmp_path / "2512.03231"
    p_dir.mkdir(parents=True)
    (p_dir / "refs.json").write_text(
        json.dumps({"arxiv_id": "2512.03231", "references": []})
    )

    summary = crawl_references_for_corpus(
        ["2512.03231"],
        output_dir=tmp_path,
        api_key="dummy_key",
    )
    assert summary["cached"] == 1
    assert summary["crawled"] == 0
    assert (tmp_path / ".refs_checkpoint.json").exists()
