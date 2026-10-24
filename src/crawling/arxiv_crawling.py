
import arxiv
import csv
import json
import re
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup


# =========================
# CONFIGURATION
# =========================

PREFIX = "2602"
START_ID = 14870
END_ID = 14874  # Test 5 papers first

BATCH_SIZE = 100
DELAY_SECONDS = 3

session = requests.Session()
session.headers.update({
    "User-Agent": "IntroDSMetadataCrawler/1.0 (academic project)"
})


# =========================
# GET ALL VERSIONS
# =========================

def get_versions(base_id):
    url = f"https://arxiv.org/abs/{base_id}"

    try:
        response = session.get(url, timeout=30)

        if response.status_code == 404:
            print(f"Paper not found: {base_id}")
            return []

        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")
        history = soup.select_one("div.submission-history")

        if history:
            text = history.get_text(" ", strip=True)
        else:
            text = soup.get_text(" ", strip=True)

        version_numbers = {
            int(number)
            for number in re.findall(r"\bv(\d+)\b", text)
        }

        if not version_numbers:
            return [base_id]

        return [
            f"{base_id}v{number}"
            for number in range(1, max(version_numbers) + 1)
        ]

    except requests.RequestException as error:
        print(f"Cannot get history for {base_id}: {error}")
        return []


# =========================
# CRAWL PAPER METADATA
# =========================

def crawl_metadata(version_ids):
    client = arxiv.Client(
        page_size=100,
        delay_seconds=DELAY_SECONDS,
        num_retries=3
    )

    records = []

    for start in range(0, len(version_ids), BATCH_SIZE):
        batch = version_ids[start:start + BATCH_SIZE]

        print(
            f"Fetching versions {start + 1}-"
            f"{start + len(batch)} of {len(version_ids)}"
        )

        search = arxiv.Search(
            id_list=batch,
            max_results=len(batch)
        )

        try:
            for paper in client.results(search):
                paper_id = paper.entry_id.rstrip("/").split("/")[-1]

                record = {
                    "id": paper_id,
                    "title": paper.title,
                    "authors": [
                        author.name for author in paper.authors
                    ],
                    "abstract": paper.summary,
                    "published": paper.published.isoformat(),
                    "updated": paper.updated.isoformat(),
                    "categories": paper.categories,
                    "primary_category": paper.primary_category,
                    "comment": paper.comment,
                    "journal_ref": paper.journal_ref,
                    "doi": paper.doi,
                    "pdf_url": paper.pdf_url
                }

                records.append(record)
                print(f"Retrieved: {paper_id}")

        except Exception as error:
            print(f"Error fetching batch: {error}")

    return records


# =========================
# SAVE JSONL
# =========================

def save_jsonl(records):
    project_root = Path(__file__).resolve().parents[2]
    output_dir = project_root / "data" / "raw"
    output_dir.mkdir(parents=True, exist_ok=True)

    output_file = output_dir / "arxiv_metadata_2602_test.jsonl"

    with output_file.open("w", encoding="utf-8") as file:
        for record in records:
            file.write(
                json.dumps(record, ensure_ascii=False) + "\n"
            )

    print(f"\nSaved {len(records)} records.")
    print(f"JSONL file: {output_file}")


# =========================
# MAIN
# =========================

def main():
    base_ids = [
        f"{PREFIX}.{number:05d}"
        for number in range(START_ID, END_ID + 1)
    ]

    all_version_ids = []

    for index, base_id in enumerate(base_ids, start=1):
        print(f"\nChecking history {index}/{len(base_ids)}: {base_id}")

        versions = get_versions(base_id)
        all_version_ids.extend(versions)

        time.sleep(DELAY_SECONDS)

    all_version_ids = list(dict.fromkeys(all_version_ids))

    print(f"\nFound {len(all_version_ids)} version IDs.")

    records = crawl_metadata(all_version_ids)
    save_jsonl(records)


if __name__ == "__main__":
    main()
