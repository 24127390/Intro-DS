"""Unit tests for dataset_exporter module."""

import json
from pathlib import Path
import pandas as pd

from src.crawling.dataset_exporter import export_tables


def test_export_tables(tmp_path: Path) -> None:
    """Test exporting raw corpus into parquet tables conforming to spec."""
    corpus_dir = tmp_path / "corpus"
    corpus_dir.mkdir()

    # Create dummy paper 1
    p1_dir = corpus_dir / "2512.03231"
    p1_dir.mkdir()
    meta1 = {
        "arxiv_id": "2512.03231",
        "title": "Quantum Foundations",
        "authors": ["Alice Doe", "Bob Smith"],
        "submission_date": "2025-12-01T08:00:00Z",
        "revised_dates": ["2025-12-03T10:00:00Z"],
        "venue": "Phys Rev A",
    }
    with (p1_dir / "metadata.json").open("w", encoding="utf-8") as f:
        json.dump(meta1, f)

    refs1 = {
        "arxiv_id": "2512.03231",
        "s2_paper_id": "s2_p1",
        "references": [
            {
                "ref_s2_id": "s2_ref_arxiv",
                "ref_arxiv_id": "2001.01234",
                "ref_title": "Prior Arxiv Work",
                "ref_authors": ["Carol White"],
                "ref_submission_date": "2020-01-01",
            },
            {
                "ref_s2_id": "s2_ref_book",
                "ref_arxiv_id": None,  # Non-arXiv reference: should be excluded from parquet
                "ref_title": "Old Book",
                "ref_authors": ["David Black"],
                "ref_submission_date": "1995",
            },
        ],
    }
    with (p1_dir / "refs.json").open("w", encoding="utf-8") as f:
        json.dump(refs1, f)

    out_dir = tmp_path / "processed"
    res = export_tables("24127559", corpus_dir=corpus_dir, output_dir=out_dir)

    assert res["papers_count"] == 1
    # Only 1 reference has an arXiv ID
    assert res["references_count"] == 1

    papers_df = pd.read_parquet(res["papers_path"])
    refs_df = pd.read_parquet(res["references_path"])

    assert list(papers_df.columns) == [
        "arxiv_id",
        "title",
        "authors",
        "submission_date",
        "revised_dates",
        "venue",
    ]
    assert papers_df.iloc[0]["arxiv_id"] == "2512.03231"
    assert papers_df.iloc[0]["venue"] == "Phys Rev A"

    assert list(refs_df.columns) == [
        "arxiv_id",
        "ref_arxiv_id",
        "ref_title",
        "ref_authors",
        "ref_submission_date",
        "ref_s2_id",
    ]
    assert refs_df.iloc[0]["arxiv_id"] == "2512.03231"
    assert refs_df.iloc[0]["ref_arxiv_id"] == "2001.01234"
    assert refs_df.iloc[0]["ref_s2_id"] == "s2_ref_arxiv"
