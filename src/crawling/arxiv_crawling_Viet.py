
import csv
import json
import random
import re
import time
from pathlib import Path

import arxiv
import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


# ==================================================
# CONFIGURATION
# ==================================================

PREFIX = "2511"
START_ID = 16709
END_ID = 21708  # Inclusive: 4,999 base IDs

BATCH_SIZE = 50
DELAY_SECONDS = 3
MAX_HISTORY_ATTEMPTS = 5
MAX_BATCH_ATTEMPTS = 5
REQUEST_TIMEOUT = 30

PROJECT_ROOT = Path(__file__).resolve().parents[2]
OUTPUT_DIR = PROJECT_ROOT / "data" / "raw"

METADATA_FILE = OUTPUT_DIR / "arxiv_metadata_2511_all_versions_Viet.jsonl"
HISTORY_FILE = OUTPUT_DIR / "arxiv_history_2511_Viet.jsonl"
HISTORY_STATUS_FILE = OUTPUT_DIR / "arxiv_history_status_2511_Viet.csv"
FAILED_FILE = OUTPUT_DIR / "arxiv_failed_2511_Viet.jsonl"

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


# ==================================================
# HTTP SESSION
# ==================================================

session = requests.Session()
session.headers.update({
    "User-Agent": "IntroDSMetadataCrawler/1.0 (academic project)"
})

retry_strategy = Retry(
    total=3,
    backoff_factor=2,
    status_forcelist=[429, 500, 502, 503, 504],
    allowed_methods=["GET"],
    respect_retry_after_header=True
)

adapter = HTTPAdapter(max_retries=retry_strategy)
session.mount("https://", adapter)
session.mount("http://", adapter)


# ==================================================
# JSONL HELPERS
# ==================================================

def read_jsonl(path):
    records = []

    if not path.exists():
        return records

    with path.open("r", encoding="utf-8") as file:
        for line in file:
            try:
                if line.strip():
                    records.append(json.loads(line))
            except json.JSONDecodeError:
                print(f"Warning: skipping invalid JSON line in {path}")

    return records


def append_jsonl(path, records):
    if not records:
        return

    with path.open("a", encoding="utf-8") as file:
        for record in records:
            file.write(json.dumps(record, ensure_ascii=False) + "\n")

        file.flush()


def save_history_status(status):
    """Rewrite the status file atomically."""
    temp_file = HISTORY_STATUS_FILE.with_suffix(".tmp")

    with temp_file.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=["base_id", "status", "message"]
        )
        writer.writeheader()

        for base_id, result in sorted(status.items()):
            writer.writerow({
                "base_id": base_id,
                "status": result["status"],
                "message": result.get("message", "")
            })

    temp_file.replace(HISTORY_STATUS_FILE)


def save_failures(failures):
    temp_file = FAILED_FILE.with_suffix(".tmp")

    with temp_file.open("w", encoding="utf-8") as file:
        for failure in failures:
            file.write(
                json.dumps(failure, ensure_ascii=False) + "\n"
            )

    temp_file.replace(FAILED_FILE)


# ==================================================
# GET PAPER VERSION HISTORY
# ==================================================

def get_versions(base_id):
    """
    Return all version IDs for a paper, e.g.:
    2602.14870v1, 2602.14870v2.

    None means the request failed and should be retried later.
    An empty list means the paper was not found.
    """
    url = f"https://arxiv.org/abs/{base_id}"

    for attempt in range(1, MAX_HISTORY_ATTEMPTS + 1):
        try:
            response = session.get(url, timeout=REQUEST_TIMEOUT)

            if response.status_code == 404:
                return []

            response.raise_for_status()

            soup = BeautifulSoup(response.text, "html.parser")
            history = soup.select_one("div.submission-history")

            if history is None:
                # Do not assume that a missing HTML element means
                # the paper has only one version.
                raise ValueError("Submission history not found in HTML")

            history_text = history.get_text(" ", strip=True)

            version_numbers = {
                int(number)
                for number in re.findall(r"\bv(\d+)\b", history_text)
            }

            if not version_numbers:
                raise ValueError("No version numbers found in history")

            max_version = max(version_numbers)

            return [
                f"{base_id}v{number}"
                for number in range(1, max_version + 1)
            ]

        except (requests.RequestException, ValueError) as error:
            wait = min(2 ** attempt + random.random(), 60)

            print(
                f"History attempt {attempt}/{MAX_HISTORY_ATTEMPTS} "
                f"failed for {base_id}: {error}"
            )

            if attempt < MAX_HISTORY_ATTEMPTS:
                time.sleep(wait)

    return None


def crawl_histories():
    """
    Resume from saved history checkpoints.
    Successfully processed IDs are not requested again.
    """
    status = {}

    if HISTORY_STATUS_FILE.exists():
        with HISTORY_STATUS_FILE.open(
            "r", encoding="utf-8", newline=""
        ) as file:
            for row in csv.DictReader(file):
                status[row["base_id"]] = {
                    "status": row["status"],
                    "message": row.get("message", "")
                }

    saved_histories = read_jsonl(HISTORY_FILE)
    saved_base_ids = {
        record["base_id"] for record in saved_histories
        if "base_id" in record
    }

    total = END_ID - START_ID + 1

    for index, number in enumerate(
        range(START_ID, END_ID + 1), start=1
    ):
        base_id = f"{PREFIX}.{number:05d}"

        previous = status.get(base_id, {})

        if (
            previous.get("status") == "success"
            and base_id in saved_base_ids
        ):
            continue

        if previous.get("status") == "not_found":
            continue

        print(f"\nHistory {index}/{total}: {base_id}")

        versions = get_versions(base_id)

        if versions is None:
            status[base_id] = {
                "status": "error",
                "message": "History request failed after retries"
            }

        elif not versions:
            status[base_id] = {
                "status": "not_found",
                "message": "HTTP 404"
            }

        else:
            append_jsonl(HISTORY_FILE, [{
                "base_id": base_id,
                "versions": versions
            }])

            saved_base_ids.add(base_id)

            status[base_id] = {
                "status": "success",
                "message": f"{len(versions)} version(s)"
            }

            print(f"Found {len(versions)} version(s)")

        save_history_status(status)
        time.sleep(DELAY_SECONDS)

    return status


# ==================================================
# CRAWL METADATA
# ==================================================

def make_record(paper):
    return {
        "id": paper.entry_id.rstrip("/").split("/")[-1],
        "title": paper.title,
        "authors": [author.name for author in paper.authors],
        "abstract": paper.summary,
        "published": (
            paper.published.isoformat() if paper.published else None
        ),
        "updated": (
            paper.updated.isoformat() if paper.updated else None
        ),
        "categories": paper.categories,
        "primary_category": paper.primary_category,
        "comment": paper.comment,
        "journal_ref": paper.journal_ref,
        "doi": paper.doi,
        "pdf_url": paper.pdf_url
    }


def crawl_metadata(all_version_ids):
    saved_records = read_jsonl(METADATA_FILE)

    seen_ids = {
        record["id"] for record in saved_records
        if record.get("id")
    }

    pending_ids = [
        paper_id for paper_id in all_version_ids
        if paper_id not in seen_ids
    ]

    print(f"\nKnown metadata records: {len(seen_ids)}")
    print(f"Versions still to fetch: {len(pending_ids)}")

    client = arxiv.Client(
        page_size=min(BATCH_SIZE, 100),
        delay_seconds=DELAY_SECONDS,
        num_retries=3
    )

    failures = read_jsonl(FAILED_FILE)

    # Remove old metadata-failure records for IDs that are being retried.
    pending_set = set(pending_ids)
    failures = [
        failure for failure in failures
        if not (
            failure.get("stage") == "metadata"
            and failure.get("id") in pending_set
        )
    ]

    save_failures(failures)

    for start in range(0, len(pending_ids), BATCH_SIZE):
        batch = pending_ids[start:start + BATCH_SIZE]
        expected_ids = set(batch)
        received = {}

        print(
            f"\nMetadata batch "
            f"{start + 1}-{start + len(batch)} "
            f"of {len(pending_ids)}"
        )

        for attempt in range(1, MAX_BATCH_ATTEMPTS + 1):
            try:
                search = arxiv.Search(
                    id_list=batch,
                    max_results=len(batch)
                )

                for paper in client.results(search):
                    record = make_record(paper)
                    paper_id = record["id"]

                    if paper_id in expected_ids:
                        received[paper_id] = record

                missing = expected_ids - set(received)

                if not missing:
                    break

                print(
                    f"Attempt {attempt}: "
                    f"{len(missing)} version(s) not returned"
                )

            except Exception as error:
                print(
                    f"Batch attempt {attempt}/"
                    f"{MAX_BATCH_ATTEMPTS} failed: {error}"
                )

            if attempt < MAX_BATCH_ATTEMPTS:
                time.sleep(min(2 ** attempt + random.random(), 60))

        # Save every successful result immediately.
        new_records = [
            received[paper_id]
            for paper_id in batch
            if paper_id in received
        ]

        append_jsonl(METADATA_FILE, new_records)

        seen_ids.update(record["id"] for record in new_records)

        # Record unresolved IDs instead of silently losing them.
        for paper_id in batch:
            if paper_id not in received:
                failures.append({
                    "id": paper_id,
                    "stage": "metadata",
                    "error": "Not returned after retries",
                    "timestamp": time.strftime(
                        "%Y-%m-%dT%H:%M:%S%z"
                    )
                })

        save_failures(failures)

        print(
            f"Saved {len(new_records)} records; "
            f"total saved: {len(seen_ids)}"
        )


# ==================================================
# MAIN
# ==================================================

def main():
    print("=" * 60)
    print("arXiv Metadata Crawler")
    print(f"ID range: {PREFIX}.{START_ID} - {PREFIX}.{END_ID}")
    print(f"Expected base IDs: {END_ID - START_ID + 1}")
    print("=" * 60)

    history_status = crawl_histories()

    history_records = read_jsonl(HISTORY_FILE)

    all_version_ids = list(dict.fromkeys(
        version_id
        for record in history_records
        for version_id in record.get("versions", [])
    ))

    print("\nHistory summary:")
    print(f"Base IDs with history: {len(history_records)}")
    print(f"Version IDs discovered: {len(all_version_ids)}")
    print(
        "History errors:",
        sum(
            result["status"] == "error"
            for result in history_status.values()
        )
    )
    print(
        "Not found:",
        sum(
            result["status"] == "not_found"
            for result in history_status.values()
        )
    )

    crawl_metadata(all_version_ids)

    final_records = read_jsonl(METADATA_FILE)

    print("\n" + "=" * 60)
    print("CRAWLER FINISHED")
    print(f"Version IDs discovered: {len(all_version_ids)}")
    print(f"Metadata records saved: {len(final_records)}")
    print(f"Metadata output: {METADATA_FILE}")
    print(f"History output: {HISTORY_FILE}")
    print(f"History status: {HISTORY_STATUS_FILE}")
    print(f"Failure log: {FAILED_FILE}")
    print("=" * 60)


if __name__ == "__main__":
    main()
