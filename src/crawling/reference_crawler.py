"""Semantic Scholar reference crawler module.

Fetches structured citation and reference metadata via Semantic Scholar API
with strict client-side rate limiting and exponential backoff for unauthenticated use.
"""

from collections import deque
import json
import os
from pathlib import Path
import time
from typing import Any, Deque, Dict, List, Optional
import urllib.error
import urllib.parse
import urllib.request

from tqdm import tqdm

S2_BASE_URL = "https://api.semanticscholar.org/graph/v1/paper"
S2_FIELDS = "references.title,references.authors,references.publicationDate,references.externalIds,references.paperId"
MIN_REQUEST_INTERVAL = 1.1  # > 1 req/sec
WINDOW_SECONDS = 300.0  # 5 minutes
MAX_REQUESTS_PER_WINDOW = 95  # Safe margin below unauthenticated 100 limit


class S2RateLimiter:
    """Sliding-window rate limiter for unauthenticated Semantic Scholar calls."""

    def __init__(
        self,
        min_interval: float = MIN_REQUEST_INTERVAL,
        window_size: float = WINDOW_SECONDS,
        max_in_window: int = MAX_REQUESTS_PER_WINDOW,
    ):
        self.min_interval = min_interval
        self.window_size = window_size
        self.max_in_window = max_in_window
        self.last_request_time = 0.0
        self.request_timestamps: Deque[float] = deque()

    def wait(self) -> None:
        """Enforce rate limits before dispatching an HTTP request."""
        now = time.time()

        # 1. Enforce minimum interval between consecutive requests
        elapsed_since_last = now - self.last_request_time
        if elapsed_since_last < self.min_interval:
            time.sleep(self.min_interval - elapsed_since_last)
            now = time.time()

        # 2. Prune timestamps outside current sliding window
        while self.request_timestamps and (now - self.request_timestamps[0] > self.window_size):
            self.request_timestamps.popleft()

        # 3. If window is full, sleep until oldest timestamp expires
        if len(self.request_timestamps) >= self.max_in_window:
            oldest = self.request_timestamps[0]
            sleep_needed = (oldest + self.window_size) - now + 0.5
            if sleep_needed > 0:
                time.sleep(sleep_needed)
                now = time.time()

        self.last_request_time = now
        self.request_timestamps.append(now)


_rate_limiter = S2RateLimiter()


def _parse_reference_item(raw_ref: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize a raw reference entry from Semantic Scholar API response.

    Args:
        raw_ref: Dict containing reference fields from S2.

    Returns:
        Structured reference dictionary.
    """
    ext_ids = raw_ref.get("externalIds") or {}
    ref_arxiv_id = ext_ids.get("ArXiv") or ext_ids.get("arxiv")

    authors_list: List[str] = []
    for auth in raw_ref.get("authors") or []:
        if isinstance(auth, dict) and auth.get("name"):
            authors_list.append(auth["name"])
        elif isinstance(auth, str):
            authors_list.append(auth)

    return {
        "ref_s2_id": raw_ref.get("paperId") or "",
        "ref_arxiv_id": ref_arxiv_id,
        "ref_title": raw_ref.get("title") or "",
        "ref_authors": authors_list,
        "ref_submission_date": raw_ref.get("publicationDate") or "",
        "has_arxiv_id": bool(ref_arxiv_id),
    }


def fetch_paper_references(
    arxiv_id: str,
    api_key: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Query Semantic Scholar API for paper references with retries and rate limiting.

    Args:
        arxiv_id: Base arXiv ID, e.g. '2512.03231'.
        api_key: Optional Semantic Scholar API key.

    Returns:
        Structured reference record, or None on permanent failure.
    """
    key = api_key or os.environ.get("S2_API_KEY")
    query_params = urllib.parse.urlencode({"fields": S2_FIELDS})
    url = f"{S2_BASE_URL}/arXiv:{arxiv_id}?{query_params}"

    headers = {
        "User-Agent": "IntroDS-ReferenceCrawler/1.0 (Course Project Group 13)",
    }
    if key:
        headers["x-api-key"] = key

    req = urllib.request.Request(url, headers=headers)
    max_retries = 4
    backoff = 10.0

    for attempt in range(max_retries):
        if not key:
            _rate_limiter.wait()
        else:
            time.sleep(0.5)

        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                s2_paper_id = data.get("paperId", "")
                raw_refs = data.get("references") or []

                parsed_refs = [_parse_reference_item(r) for r in raw_refs]

                return {
                    "arxiv_id": arxiv_id,
                    "s2_paper_id": s2_paper_id,
                    "references": parsed_refs,
                }
        except urllib.error.HTTPError as err:
            if err.code == 404:
                # Paper not indexed yet by Semantic Scholar
                return {
                    "arxiv_id": arxiv_id,
                    "s2_paper_id": "",
                    "references": [],
                    "note": "not_found_on_s2",
                }
            elif err.code == 429:
                # Rate limited: apply exponential backoff
                time.sleep(backoff)
                backoff *= 2.0
                continue
            else:
                return None
        except Exception:
            time.sleep(5.0)
            continue

    return None


def crawl_references_for_paper(
    arxiv_id: str,
    output_dir: Path = Path("data/raw/corpus"),
    api_key: Optional[str] = None,
) -> bool:
    """Crawl and persist references for a given paper.

    Args:
        arxiv_id: Base arXiv ID.
        output_dir: Corpus root directory.
        api_key: Optional API key.

    Returns:
        True if fetched or cached successfully, False otherwise.
    """
    paper_dir = output_dir / arxiv_id
    refs_file = paper_dir / "refs.json"

    if refs_file.exists():
        return True

    paper_dir.mkdir(parents=True, exist_ok=True)
    res = fetch_paper_references(arxiv_id, api_key=api_key)
    if res is None:
        return False

    with refs_file.open("w", encoding="utf-8") as f:
        json.dump(res, f, indent=2, ensure_ascii=False)

    # Also save copy in each version directory if versions exist
    for v_dir in paper_dir.glob("v*"):
        if v_dir.is_dir():
            v_refs = v_dir / "refs.json"
            if not v_refs.exists():
                with v_refs.open("w", encoding="utf-8") as f:
                    json.dump(res, f, indent=2, ensure_ascii=False)

    return True


def crawl_references_for_corpus(
    arxiv_ids: List[str],
    output_dir: Path = Path("data/raw/corpus"),
    api_key: Optional[str] = None,
) -> Dict[str, Any]:
    """Crawl references for all given paper IDs.

    Args:
        arxiv_ids: List of base arXiv IDs.
        output_dir: Corpus root directory.
        api_key: Optional API key.

    Returns:
        Summary dict of crawled, cached, and failed counts.
    """
    checkpoint_file = output_dir / ".refs_checkpoint.json"
    completed: set[str] = set()

    if checkpoint_file.exists():
        try:
            with checkpoint_file.open("r", encoding="utf-8") as f:
                completed = set(json.load(f))
        except Exception:
            completed = set()

    cached_count = 0
    crawled_count = 0
    failed_count = 0

    pbar = tqdm(arxiv_ids, desc="Crawling References (S2 API)", unit="paper")

    for aid in pbar:
        if aid in completed or (output_dir / aid / "refs.json").exists():
            cached_count += 1
            completed.add(aid)
            pbar.set_postfix({"cached": cached_count, "new": crawled_count, "failed": failed_count})
            continue

        ok = crawl_references_for_paper(aid, output_dir=output_dir, api_key=api_key)
        if ok:
            crawled_count += 1
            completed.add(aid)
        else:
            failed_count += 1

        pbar.set_postfix({"cached": cached_count, "new": crawled_count, "failed": failed_count})

        # Periodically save checkpoint
        if (crawled_count + cached_count) % 20 == 0:
            with checkpoint_file.open("w", encoding="utf-8") as f:
                json.dump(list(completed), f)

    with checkpoint_file.open("w", encoding="utf-8") as f:
        json.dump(list(completed), f)

    return {
        "total": len(arxiv_ids),
        "cached": cached_count,
        "crawled": crawled_count,
        "failed": failed_count,
    }
