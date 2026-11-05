"""Unit tests for the generate_report_stats utility."""

import json
from pathlib import Path

import pandas as pd

from src.utils.generate_report_stats import (
    _format_bytes,
    compute_milestone1_stats,
    format_markdown_report,
)


def test_format_bytes() -> None:
    """Test byte formatting into human-readable strings."""
    assert _format_bytes(500) == "500.00 B"
    assert _format_bytes(2048) == "2.00 KB"
    assert _format_bytes(1048576 * 5) == "5.00 MB"


def test_compute_milestone1_stats(tmp_path: Path) -> None:
    """Test computing crawling and figure removal statistics on mock data."""
    corpus_dir = tmp_path / "corpus"
    processed_dir = tmp_path / "processed"

    # Setup mock paper with 2 versions
    p_dir = corpus_dir / "2512.03231"
    v1_dir = p_dir / "v1"
    v2_dir = p_dir / "v2"
    v1_dir.mkdir(parents=True)
    v2_dir.mkdir(parents=True)

    # metadata & refs
    (p_dir / "metadata.json").write_text(
        json.dumps({"arxiv_id": "2512.03231", "versions": ["2512.03231v1", "2512.03231v2"]})
    )
    (p_dir / "refs.json").write_text(
        json.dumps({
            "arxiv_id": "2512.03231",
            "references": [{"ref_arxiv_id": "2503.14454", "ref_title": "Cited Paper"}],
        })
    )

    # Mock tex and bib files
    (v1_dir / "main.tex").write_text("\\documentclass{article}")
    (v1_dir / "refs.bib").write_text("@article{test}")
    (v1_dir / ".size_stats.json").write_text(
        json.dumps({
            "raw_uncompressed_bytes": 1000000,
            "clean_extracted_bytes": 200000,
            "files_extracted": 2,
        })
    )

    (v2_dir / "main.tex").write_text("\\documentclass{article}")
    (v2_dir / ".size_stats.json").write_text(
        json.dumps({
            "raw_uncompressed_bytes": 1200000,
            "clean_extracted_bytes": 240000,
            "files_extracted": 1,
        })
    )

    # Mock parquet tables
    student_dir = processed_dir / "24127559"
    student_dir.mkdir(parents=True)
    pd.DataFrame([{"arxiv_id": "2512.03231"}]).to_parquet(student_dir / "papers.parquet")
    pd.DataFrame([{
        "arxiv_id": "2512.03231",
        "ref_arxiv_id": "2503.14454",
    }]).to_parquet(student_dir / "references.parquet")

    stats = compute_milestone1_stats(
        student_id="24127559",
        corpus_dir=corpus_dir,
        processed_dir=processed_dir,
    )

    assert stats["discovered_papers"] == 1
    assert stats["papers_with_sources"] == 1
    assert stats["total_versions"] == 2
    assert stats["total_tex_files"] == 2
    assert stats["total_bib_files"] == 1
    assert stats["figure_removal"]["total_raw_bytes"] == 2200000
    assert stats["figure_removal"]["total_clean_bytes"] == 440000
    assert stats["figure_removal"]["reduction_pct"] == 80.0
    assert stats["references"]["filtered_arxiv_references"] == 1
    assert stats["references"]["avg_refs_per_paper"] == 1.0

    report = format_markdown_report(stats)
    assert "# Milestone 1: Data Crawling — Experimental Statistics" in report
    assert "80.0%" in report
