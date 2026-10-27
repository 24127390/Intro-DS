"""Entry discovery module using arXiv API.

Resolves paper IDs into structured metadata including version lists.
"""

from datetime import datetime
import json
from pathlib import Path
import re
import time
from typing import Any, Dict, List, Optional, Set

import arxiv
from tqdm import tqdm

ARXIV_RATE_LIMIT_DELAY = 3.0  # arXiv etiquette: >= 3 seconds between calls
BATCH_SIZE = 100


def _parse_version_info(short_id: str, base_id: str) -> List[str]:
    """Extract list of versions from short arXiv ID.

    Args:
        short_id: String such as '2512.03231v4' or '2512.03231'.
        base_id: Canonical base ID such as '2512.03231'.

    Returns:
        List of version identifiers, e.g. ['2512.03231v1', ..., '2512.03231v4'].
    """
    match = re.search(r"v(\d+)$", short_id)
    if match:
        version_count = int(match.group(1))
    else:
        version_count = 1

    return [f"{base_id}v{i}" for i in range(1, version_count + 1)]


def _format_datetime(dt: Optional[datetime]) -> Optional[str]:
    """Format datetime object into ISO 8601 string."""
    if dt is None:
        return None
    return dt.isoformat()


def serialize_arxiv_result(result: arxiv.Result, base_id: str) -> Dict[str, Any]:
    """Convert an arxiv.Result object into our standardized metadata dictionary.

    Args:
        result: The arxiv.Result object returned by the API client.
        base_id: The expected base arXiv ID.

    Returns:
        Structured metadata dictionary conforming to milestone requirements.
    """
    short_id = result.get_short_id()
    versions = _parse_version_info(short_id, base_id)

    pub_iso = _format_datetime(result.published)
    upd_iso = _format_datetime(result.updated)

    revised_dates: List[str] = []
    if upd_iso and pub_iso and upd_iso != pub_iso:
        revised_dates.append(upd_iso)

    return {
        "arxiv_id": base_id,
        "title": result.title.strip() if result.title else "",
        "authors": [a.name for a in result.authors] if result.authors else [],
        "submission_date": pub_iso,
        "revised_dates": revised_dates,
        "abstract": result.summary.strip() if result.summary else "",
        "venue": result.journal_ref or None,
        "primary_category": result.primary_category,
        "categories": result.categories,
        "doi": result.doi,
        "versions": versions,
    }


def discover_papers(
    arxiv_ids: List[str],
    output_dir: Path = Path("data/raw/corpus"),
    batch_size: int = BATCH_SIZE,
    delay: float = ARXIV_RATE_LIMIT_DELAY,
) -> Dict[str, Any]:
    """Discover metadata and version lists for a list of arXiv IDs.

    Args:
        arxiv_ids: List of base arXiv IDs to resolve (e.g. ['2512.03231', ...]).
        output_dir: Root directory for corpus storage (e.g. data/raw/corpus).
        batch_size: Number of IDs per API query batch.
        delay: Sleep interval between queries to respect API etiquette.

    Returns:
        Summary dictionary with counts of resolved, cached, and failed papers.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    failed_log_path = output_dir.parent / "failed_ids.txt"

    # Check which IDs are already resolved
    pending_ids: List[str] = []
    cached_count = 0

    for aid in arxiv_ids:
        meta_file = output_dir / aid / "metadata.json"
        if meta_file.exists():
            cached_count += 1
        else:
            pending_ids.append(aid)

    if not pending_ids:
        return {
            "total": len(arxiv_ids),
            "cached": cached_count,
            "resolved": 0,
            "failed": 0,
        }

    client = arxiv.Client(
        page_size=batch_size,
        delay_seconds=delay,
        num_retries=3,
    )

    resolved_count = 0
    failed_ids: List[str] = []

    # Process in batches
    for i in range(0, len(pending_ids), batch_size):
        chunk = pending_ids[i : i + batch_size]
        search = arxiv.Search(id_list=chunk)

        found_in_batch: Set[str] = set()

        try:
            for result in client.results(search):
                short_id = result.get_short_id()
                # Clean version suffix from short_id to get base id
                base_id = re.sub(r"v\d+$", "", short_id)

                if base_id not in chunk:
                    # Match against original chunk in case of formatting discrepancies
                    for cand in chunk:
                        if cand in short_id or short_id in cand:
                            base_id = cand
                            break

                found_in_batch.add(base_id)
                data = serialize_arxiv_result(result, base_id)

                paper_dir = output_dir / base_id
                paper_dir.mkdir(parents=True, exist_ok=True)
                with (paper_dir / "metadata.json").open("w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)

                resolved_count += 1

        except Exception as exc:
            # Catch transient network / parsing error for batch
            for aid in chunk:
                if aid not in found_in_batch:
                    failed_ids.append(aid)
            time.sleep(delay * 2)
            continue

        # Check for missing IDs in chunk
        for aid in chunk:
            if aid not in found_in_batch:
                failed_ids.append(aid)

        time.sleep(delay)

    # Persist failed IDs if any
    if failed_ids:
        failed_log_path.parent.mkdir(parents=True, exist_ok=True)
        with failed_log_path.open("a", encoding="utf-8") as f:
            for fid in failed_ids:
                f.write(f"{fid}\n")

    return {
        "total": len(arxiv_ids),
        "cached": cached_count,
        "resolved": resolved_count,
        "failed": len(failed_ids),
    }
