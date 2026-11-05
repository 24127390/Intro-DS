"""Automated statistics and performance measurement tool for Milestone 1.

Computes key metrics required for the course report (Jupyter Book):
1. Crawling coverage and overall success rate.
2. Average paper size before vs after removing figure binaries (bandwidth/disk savings).
3. Citation statistics (average, median, min, max references per paper).
4. Reference extraction success rate.
5. Disk storage and memory footprint.
"""

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from src.crawling.config_reader import load_member_range


def _format_bytes(size: float) -> str:
    """Format bytes into human-readable string (KB, MB, GB)."""
    for unit in ["B", "KB", "MB", "GB"]:
        if abs(size) < 1024.0 or unit == "GB":
            return f"{size:.2f} {unit}"
        size /= 1024.0
    return f"{size:.2f} GB"


def _dir_size_bytes(directory: Path) -> int:
    """Recursively calculate total bytes of all files in a directory."""
    if not directory.exists():
        return 0
    return sum(f.stat().st_size for f in directory.rglob("*") if f.is_file())


def compute_milestone1_stats(
    student_id: str,
    corpus_dir: Path = Path("data/raw/corpus"),
    processed_dir: Path = Path("data/processed"),
    failed_log_path: Path = Path("data/raw/failed_ids.txt"),
) -> Dict[str, Any]:
    """Compute all required metrics for Milestone 1 crawling report.

    Args:
        student_id: Student ID string (e.g. '24127559').
        corpus_dir: Root directory of raw scraped corpus.
        processed_dir: Root directory of processed parquet datasets.
        failed_log_path: Path to the log of failed arXiv IDs.

    Returns:
        Structured dictionary of computed statistics and analysis.
    """
    member_info = load_member_range(student_id)
    assigned_ids: List[str] = member_info["arxiv_ids"]
    total_assigned = len(assigned_ids)

    # 1. Discovery and Crawling Coverage
    discovered_papers = 0
    papers_with_sources = 0
    papers_with_refs = 0
    total_versions = 0
    total_tex_files = 0
    total_bib_files = 0

    raw_sizes: List[int] = []
    clean_sizes: List[int] = []

    total_s2_references = 0
    papers_with_valid_s2 = 0

    if corpus_dir.exists():
        for p_dir in corpus_dir.iterdir():
            if not p_dir.is_dir() or p_dir.name.startswith("."):
                continue

            meta_file = p_dir / "metadata.json"
            if meta_file.exists():
                discovered_papers += 1

            refs_file = p_dir / "refs.json"
            if refs_file.exists():
                try:
                    with refs_file.open("r", encoding="utf-8") as rf:
                        r_data = json.load(rf)
                    raw_refs = r_data.get("references", [])
                    if raw_refs or r_data.get("note") != "not_found_on_s2":
                        papers_with_valid_s2 += 1
                        total_s2_references += len(raw_refs)
                except Exception:
                    pass

            v_dirs = [v for v in p_dir.glob("v*") if v.is_dir()]
            if v_dirs:
                has_src = any(v.glob("*.tex") for v in v_dirs)
                if has_src:
                    papers_with_sources += 1

                for v in v_dirs:
                    total_versions += 1
                    total_tex_files += len(list(v.glob("*.tex")))
                    total_bib_files += len(list(v.glob("*.bib")))

                    # Check size stats
                    stats_file = v / ".size_stats.json"
                    if stats_file.exists():
                        try:
                            with stats_file.open("r", encoding="utf-8") as sf:
                                s_data = json.load(sf)
                            raw_sizes.append(s_data.get("raw_uncompressed_bytes", 0))
                            clean_sizes.append(s_data.get("clean_extracted_bytes", 0))
                        except Exception:
                            pass
                    else:
                        # Fallback: measure existing clean files
                        clean_b = sum(f.stat().st_size for f in v.glob("*") if f.is_file())
                        clean_sizes.append(clean_b)

    # Failed count
    failed_count = 0
    if failed_log_path.exists():
        with failed_log_path.open("r", encoding="utf-8") as ff:
            failed_count = len([line.strip() for line in ff if line.strip()])

    success_rate = (papers_with_sources / total_assigned * 100) if total_assigned else 0.0
    ref_success_rate = (
        (papers_with_valid_s2 / discovered_papers * 100) if discovered_papers else 0.0
    )

    # 2. Size Before & After Removing Figures
    total_raw_bytes = sum(raw_sizes)
    total_clean_bytes = sum(clean_sizes)
    avg_raw_bytes = (total_raw_bytes / len(raw_sizes)) if raw_sizes else 0
    avg_clean_bytes = (total_clean_bytes / len(clean_sizes)) if clean_sizes else 0
    bytes_saved = total_raw_bytes - total_clean_bytes
    reduction_pct = (bytes_saved / total_raw_bytes * 100) if total_raw_bytes else 0.0

    # 3. Parquet Tables & References
    student_processed_dir = processed_dir / student_id
    papers_parquet = student_processed_dir / "papers.parquet"
    refs_parquet = student_processed_dir / "references.parquet"

    parquet_papers_count = 0
    parquet_refs_count = 0
    avg_refs_per_paper = 0.0
    median_refs_per_paper = 0.0
    max_refs_per_paper = 0
    min_refs_per_paper = 0

    if papers_parquet.exists():
        df_p = pd.read_parquet(papers_parquet)
        parquet_papers_count = len(df_p)

    if refs_parquet.exists():
        df_r = pd.read_parquet(refs_parquet)
        parquet_refs_count = len(df_r)
        if not df_r.empty and "arxiv_id" in df_r.columns:
            counts = df_r.groupby("arxiv_id").size()
            avg_refs_per_paper = float(counts.mean())
            median_refs_per_paper = float(counts.median())
            max_refs_per_paper = int(counts.max())
            min_refs_per_paper = int(counts.min())

    # 4. Storage Footprint
    raw_disk_bytes = _dir_size_bytes(corpus_dir)
    parquet_disk_bytes = _dir_size_bytes(student_processed_dir)

    return {
        "student_id": student_id,
        "member_name": member_info.get("member_name", ""),
        "total_assigned": total_assigned,
        "discovered_papers": discovered_papers,
        "papers_with_sources": papers_with_sources,
        "total_versions": total_versions,
        "total_tex_files": total_tex_files,
        "total_bib_files": total_bib_files,
        "failed_papers": failed_count,
        "overall_success_rate_pct": round(success_rate, 2),
        "figure_removal": {
            "total_raw_bytes": total_raw_bytes,
            "total_clean_bytes": total_clean_bytes,
            "avg_raw_bytes": round(avg_raw_bytes),
            "avg_clean_bytes": round(avg_clean_bytes),
            "bytes_saved": bytes_saved,
            "reduction_pct": round(reduction_pct, 2),
            "total_raw_fmt": _format_bytes(total_raw_bytes),
            "total_clean_fmt": _format_bytes(total_clean_bytes),
            "avg_raw_fmt": _format_bytes(avg_raw_bytes),
            "avg_clean_fmt": _format_bytes(avg_clean_bytes),
            "bytes_saved_fmt": _format_bytes(bytes_saved),
        },
        "references": {
            "total_s2_references": total_s2_references,
            "filtered_arxiv_references": parquet_refs_count,
            "ref_success_rate_pct": round(ref_success_rate, 2),
            "avg_refs_per_paper": round(avg_refs_per_paper, 2),
            "median_refs_per_paper": round(median_refs_per_paper, 1),
            "max_refs_per_paper": max_refs_per_paper,
            "min_refs_per_paper": min_refs_per_paper,
        },
        "storage": {
            "raw_corpus_disk_bytes": raw_disk_bytes,
            "raw_corpus_disk_fmt": _format_bytes(raw_disk_bytes),
            "parquet_disk_bytes": parquet_disk_bytes,
            "parquet_disk_fmt": _format_bytes(parquet_disk_bytes),
        },
    }


def format_markdown_report(stats: Dict[str, Any]) -> str:
    """Format computed statistics into a publication-ready Markdown report."""
    fr = stats["figure_removal"]
    rf = stats["references"]
    st = stats["storage"]

    return f"""# Milestone 1: Data Crawling — Experimental Statistics

**Student**: {stats['member_name']} (ID: `{stats['student_id']}`)  
**Assigned Papers**: {stats['total_assigned']} papers

---

## 1. Crawling Coverage & Success Rate

| Metric | Count | Percentage |
|---|---|---|
| **Assigned Range** | {stats['total_assigned']} | 100.0% |
| **Discovered Metadata (`metadata.json`)** | {stats['discovered_papers']} | {stats['discovered_papers'] / max(1, stats['total_assigned']) * 100:.2f}% |
| **Completed LaTeX Sources** | {stats['papers_with_sources']} | {stats['overall_success_rate_pct']:.2f}% |
| **Total Versions Harvested** | {stats['total_versions']} | — |
| **Extracted `.tex` Files** | {stats['total_tex_files']} | — |
| **Extracted `.bib` Files** | {stats['total_bib_files']} | — |
| **Failed Papers** | {stats['failed_papers']} | — |

---

## 2. Impact of Figure Binary Removal

> Eliminating image binaries (`.png`, `.jpg`, `.pdf`, `.eps`) during in-memory unpacking
> reduced total storage by **{fr['reduction_pct']}%**.

| Metric | Uncompressed Archive (With Figures) | Extracted Sources (`.tex` + `.bib`) | Savings |
|---|---|---|---|
| **Total Size** | {fr['total_raw_fmt']} | {fr['total_clean_fmt']} | **-{fr['reduction_pct']}%** ({fr['bytes_saved_fmt']}) |
| **Average per Version** | {fr['avg_raw_fmt']} | {fr['avg_clean_fmt']} | **-{fr['reduction_pct']}%** |

---

## 3. Reference Metadata Extraction (Semantic Scholar)

| Reference Metric | Value |
|---|---|
| **Reference Crawl Success Rate** | **{rf['ref_success_rate_pct']}%** |
| **Total Raw References Retrieved** | {rf['total_s2_references']} |
| **References with arXiv ID (in `references.parquet`)** | **{rf['filtered_arxiv_references']}** |
| **Average References per Paper** | **{rf['avg_refs_per_paper']}** |
| **Median References per Paper** | {rf['median_refs_per_paper']} |
| **Range (Min – Max)** | {rf['min_refs_per_paper']} – {rf['max_refs_per_paper']} |

---

## 4. Final Output & Storage Footprint

| Storage Component | Size on Disk | Notes |
|---|---|---|
| **Raw Corpus Directory (`data/raw/corpus/`)** | {st['raw_corpus_disk_fmt']} | Selective `.tex` + `.bib` files |
| **Published Parquet Tables (`data/processed/`)** | {st['parquet_disk_fmt']} | Standardized Hugging Face tables |
"""


def main() -> None:
    """CLI entry point for computing crawling statistics."""
    parser = argparse.ArgumentParser(
        description="Compute Milestone 1 statistical metrics for Jupyter Book"
    )
    parser.add_argument(
        "--student-id",
        type=str,
        default="24127559",
        help="Target student ID (default: 24127559)",
    )
    parser.add_argument(
        "--output-md",
        type=str,
        default="",
        help="Optional path to save generated Markdown report",
    )

    args = parser.parse_args()
    stats = compute_milestone1_stats(student_id=args.student_id)
    md_content = format_markdown_report(stats)

    print(md_content)

    if args.output_md:
        out_p = Path(args.output_md)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        with out_p.open("w", encoding="utf-8") as f:
            f.write(md_content)
        print(f"\nReport written to: {out_p}")


if __name__ == "__main__":
    main()
