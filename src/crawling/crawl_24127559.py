"""Crawling pipeline runner for Nguyen Thanh Toan (student ID: 24127559).

Assigned range: 2025-12, IDs 3231 to 8230 (2512.03231 to 2512.08230).

Usage:
    python -m src.crawling.crawl_24127559 [--stage STAGE] [--offset OFFSET] [--limit LIMIT]

Stages:
    all         Execute all pipeline stages in sequence (default)
    discover    Resolve arXiv IDs to paper metadata and version lists
    download    Download full-text LaTeX sources for all versions
    references  Crawl Semantic Scholar reference metadata
    export      Export standardized papers.parquet and references.parquet
"""

import argparse
from pathlib import Path
import time
from typing import List

import psutil

from src.crawling.config_reader import load_member_range
from src.crawling.dataset_exporter import export_tables
from src.crawling.entry_discovery import discover_papers
from src.crawling.reference_crawler import crawl_references_for_corpus
from src.crawling.source_downloader import download_sources_for_corpus

STUDENT_ID = "24127559"
CORPUS_DIR = Path("data/raw/corpus")
PROCESSED_DIR = Path("data/processed")


def _get_ram_usage_mb() -> float:
    """Get current resident set size (RAM) in megabytes."""
    process = psutil.Process()
    return process.memory_info().rss / (1024 * 1024)


def run_pipeline(stage: str = "all", limit: int = 0, offset: int = 0) -> None:
    """Execute crawling stages for student 24127559.

    Args:
        stage: Which stage to execute ('all', 'discover', 'download', 'references', 'export').
        limit: If > 0, restrict execution to N assigned IDs.
        offset: Starting index within assigned IDs (default: 0).
    """
    print(f"=== Starting Crawling Pipeline for Student {STUDENT_ID} ===")
    t_start = time.perf_counter()
    ram_start = _get_ram_usage_mb()

    # 1. Load assigned range
    member_info = load_member_range(STUDENT_ID)
    all_arxiv_ids: List[str] = member_info["arxiv_ids"]
    total_assigned = len(all_arxiv_ids)

    start_idx = max(0, offset)
    end_idx = start_idx + limit if limit > 0 else total_assigned
    arxiv_ids: List[str] = all_arxiv_ids[start_idx:end_idx]

    if not arxiv_ids:
        print(
            f"No papers selected for offset={offset}, limit={limit} "
            f"(total assigned: {total_assigned})."
        )
        return

    if offset > 0 or limit > 0:
        print(
            f"Applying range: processing {len(arxiv_ids)} papers "
            f"(indices {start_idx} to {start_idx + len(arxiv_ids) - 1} of {total_assigned}): "
            f"{arxiv_ids[0]} -> {arxiv_ids[-1]}."
        )
    else:
        print(f"Total papers assigned: {total_assigned} ({arxiv_ids[0]} -> {arxiv_ids[-1]}).")

    CORPUS_DIR.mkdir(parents=True, exist_ok=True)
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

    # Stage: Discover
    if stage in ("all", "discover"):
        print("\n--- [Stage 1/4] Discovering Paper Entries (arXiv API) ---")
        t0 = time.perf_counter()
        disc_res = discover_papers(arxiv_ids, output_dir=CORPUS_DIR)
        print(
            f"Discovery completed in {time.perf_counter() - t0:.2f}s | "
            f"Cached: {disc_res['cached']}, Resolved: {disc_res['resolved']}, "
            f"Failed: {disc_res['failed']}"
        )

    # Stage: Download LaTeX Sources
    if stage in ("all", "download"):
        print("\n--- [Stage 2/4] Downloading LaTeX Sources (.tex only) ---")
        t0 = time.perf_counter()
        dl_res = download_sources_for_corpus(arxiv_ids, output_dir=CORPUS_DIR)
        print(
            f"Source download completed in {time.perf_counter() - t0:.2f}s | "
            f"Completed papers: {dl_res['completed_papers']}/{dl_res['total_papers']}"
        )

    # Stage: References Crawling
    if stage in ("all", "references"):
        print("\n--- [Stage 3/4] Crawling References (Semantic Scholar API) ---")
        t0 = time.perf_counter()
        ref_res = crawl_references_for_corpus(arxiv_ids, output_dir=CORPUS_DIR)
        print(
            f"References crawl completed in {time.perf_counter() - t0:.2f}s | "
            f"Cached: {ref_res['cached']}, Newly crawled: {ref_res['crawled']}, "
            f"Failed: {ref_res['failed']}"
        )

    # Stage: Export Tables
    if stage in ("all", "export"):
        print("\n--- [Stage 4/4] Exporting Parquet Tables ---")
        t0 = time.perf_counter()
        exp_res = export_tables(STUDENT_ID, corpus_dir=CORPUS_DIR, output_dir=PROCESSED_DIR)
        print(
            f"Export completed in {time.perf_counter() - t0:.2f}s | "
            f"Papers: {exp_res['papers_count']}, References: {exp_res['references_count']}"
        )
        print(f"Output files:\n  {exp_res['papers_path']}\n  {exp_res['references_path']}")

    total_time = time.perf_counter() - t_start
    ram_end = _get_ram_usage_mb()
    print("\n=== Pipeline Execution Summary ===")
    print(f"Total Wall Time: {total_time:.2f} seconds")
    print(f"RAM Usage: Initial = {ram_start:.1f} MB, Final = {ram_end:.1f} MB")


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description=f"arXiv crawling pipeline for student {STUDENT_ID}"
    )
    parser.add_argument(
        "--stage",
        choices=["all", "discover", "download", "references", "export"],
        default="all",
        help="Pipeline stage to execute (default: all)",
    )
    parser.add_argument(
        "--offset",
        type=int,
        default=0,
        help="Starting index offset within assigned papers (default: 0)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Process only N papers from offset (0 for all remaining)",
    )

    args = parser.parse_args()
    run_pipeline(stage=args.stage, limit=args.limit, offset=args.offset)


if __name__ == "__main__":
    main()
