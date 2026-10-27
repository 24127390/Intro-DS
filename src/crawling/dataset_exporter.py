"""Dataset exporter module for Milestone 1.

Reads local corpus files and exports standardized Parquet tables:
1. papers.parquet
2. references.parquet (only references possessing an arXiv identifier)
"""

import json
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd


def export_tables(
    student_id: str,
    corpus_dir: Path = Path("data/raw/corpus"),
    output_dir: Path = Path("data/processed"),
) -> Dict[str, Any]:
    """Export corpus into two standardized Parquet tables.

    Args:
        student_id: Student identifier (e.g. '24127559').
        corpus_dir: Path to raw corpus containing paper directories.
        output_dir: Path where processed Parquet files will be stored.

    Returns:
        Dictionary with record counts and file paths of generated tables.
    """
    dest_dir = output_dir / student_id
    dest_dir.mkdir(parents=True, exist_ok=True)

    papers_records: List[Dict[str, Any]] = []
    references_records: List[Dict[str, Any]] = []

    for paper_dir in sorted(corpus_dir.glob("*")):
        if not paper_dir.is_dir():
            continue

        base_id = paper_dir.name
        meta_file = paper_dir / "metadata.json"
        refs_file = paper_dir / "refs.json"

        # 1. Process Paper Record
        if meta_file.exists():
            try:
                with meta_file.open("r", encoding="utf-8") as f:
                    meta = json.load(f)

                papers_records.append(
                    {
                        "arxiv_id": str(meta.get("arxiv_id", base_id)),
                        "title": str(meta.get("title", "")),
                        "authors": list(meta.get("authors", [])),
                        "submission_date": str(meta.get("submission_date") or ""),
                        "revised_dates": list(meta.get("revised_dates", [])),
                        "venue": meta.get("venue") or None,
                    }
                )
            except Exception:
                pass

        # 2. Process Reference Records
        if refs_file.exists():
            try:
                with refs_file.open("r", encoding="utf-8") as f:
                    ref_data = json.load(f)

                citing_id = str(ref_data.get("arxiv_id", base_id))
                raw_refs = ref_data.get("references", [])

                for r in raw_refs:
                    ref_aid = r.get("ref_arxiv_id")
                    # Specification: Only include references that possess an arXiv ID
                    if ref_aid:
                        references_records.append(
                            {
                                "arxiv_id": citing_id,
                                "ref_arxiv_id": str(ref_aid),
                                "ref_title": str(r.get("ref_title", "")),
                                "ref_authors": list(r.get("ref_authors", [])),
                                "ref_submission_date": str(
                                    r.get("ref_submission_date") or ""
                                ),
                                "ref_s2_id": str(r.get("ref_s2_id", "")),
                            }
                        )
            except Exception:
                pass

    # Build DataFrames
    papers_df = pd.DataFrame(
        papers_records,
        columns=[
            "arxiv_id",
            "title",
            "authors",
            "submission_date",
            "revised_dates",
            "venue",
        ],
    )

    references_df = pd.DataFrame(
        references_records,
        columns=[
            "arxiv_id",
            "ref_arxiv_id",
            "ref_title",
            "ref_authors",
            "ref_submission_date",
            "ref_s2_id",
        ],
    )

    papers_path = dest_dir / "papers.parquet"
    references_path = dest_dir / "references.parquet"

    papers_df.to_parquet(papers_path, index=False, engine="pyarrow")
    references_df.to_parquet(references_path, index=False, engine="pyarrow")

    return {
        "papers_count": len(papers_df),
        "references_count": len(references_df),
        "papers_path": str(papers_path),
        "references_path": str(references_path),
    }
