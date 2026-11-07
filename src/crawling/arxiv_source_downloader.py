
import csv
import json
import re
import time
from pathlib import Path

import requests


# =========================
# CONFIGURATION
# =========================

INPUT_FILE = Path("data/raw/arxiv_metadata_2602_all_versions.jsonl")

OUTPUT_DIR = Path("data/raw/sources")
LOG_FILE = Path("data/raw/source_download_status_2602.csv")

# Test with 5 papers first.
# Set to None to download all papers.
MAX_PAPERS = 50

DELAY_SECONDS = 10
TIMEOUT = 120
MAX_RETRIES = 5

BASE_URL = "https://export.arxiv.org/e-print/"

session = requests.Session()
session.headers.update({
    "User-Agent": "IntroDSSourceDownloader/1.0 (academic project)"
})


# =========================
# LOAD METADATA
# =========================

def load_papers():
    papers = []
    seen_ids = set()

    with open(INPUT_FILE, "r", encoding="utf-8") as file:
        for line in file:
            if not line.strip():
                continue

            paper = json.loads(line)

            paper_id = paper.get("id")

            if not paper_id:
                continue

            # Avoid downloading duplicate versions
            if paper_id not in seen_ids:
                papers.append(paper)
                seen_ids.add(paper_id)

    return papers


# =========================
# PARSE ARXIV ID
# =========================

def parse_paper_id(paper_id):
    match = re.match(r"^(.+?)(?:v(\d+))?$", paper_id)

    if not match:
        raise ValueError("Invalid arXiv ID")

    base_id = match.group(1)
    version = match.group(2) or "1"

    return base_id, version


# =========================
# DOWNLOAD SOURCE
# =========================


def download_source(paper_id):

    base_id, version = parse_paper_id(paper_id)

    paper_dir = OUTPUT_DIR / base_id / f"v{version}"
    paper_dir.mkdir(parents=True, exist_ok=True)

    output_file = paper_dir / "source.tar.gz"
    temp_file = paper_dir / "source.part"

    # Skip existing files
    if output_file.exists() and output_file.stat().st_size > 0:
        return "SKIPPED", str(output_file), output_file.stat().st_size

    url = BASE_URL + paper_id

    for attempt in range(1, MAX_RETRIES + 1):

        response = None

        try:
            print(f"    Attempt {attempt}/{MAX_RETRIES}")

            response = session.get(
                url,
                stream=True,
                timeout=TIMEOUT
            )

            # Handle rate limiting
            if response.status_code == 429:
                retry_after = response.headers.get("Retry-After")

                wait_time = (
                    int(retry_after)
                    if retry_after and retry_after.isdigit()
                    else 60 * attempt
                )

                print(f"    Rate limited. Waiting {wait_time}s...")
                time.sleep(wait_time)
                continue

            response.raise_for_status()

            downloaded_size = 0
            first_bytes = b""

            with open(temp_file, "wb") as file:

                for chunk in response.iter_content(chunk_size=8192):

                    if not chunk:
                        continue

                    if downloaded_size == 0:
                        first_bytes = chunk[:512]

                    file.write(chunk)
                    downloaded_size += len(chunk)

            # Check empty file
            if downloaded_size == 0:
                raise ValueError("Empty source file")

            # Reject HTML error pages
            if first_bytes.lstrip().lower().startswith(
                (b"<!doctype html", b"<html")
            ):
                raise ValueError("Received HTML instead of source")

            # Validate archive signature
            with open(temp_file, "rb") as file:
                signature = file.read(4)

            valid_archive = (
                signature.startswith(b"\x1f\x8b")
                or signature.startswith(b"PK\x03\x04")
                or signature.startswith(b"BZh")
                or signature.startswith(b"\xfd7z")
            )

            if not valid_archive:
                raise ValueError("Unsupported archive format")

            # Move completed file to final location
            temp_file.replace(output_file)

            return "SUCCESS", str(output_file), downloaded_size

        except (requests.RequestException, ValueError, OSError) as error:

            print(f"    Error: {error}")

            temp_file.unlink(missing_ok=True)

            if attempt == MAX_RETRIES:
                return "FAILED", "", str(error)

            wait_time = 15 * attempt

            print(f"    Retrying in {wait_time} seconds...")
            time.sleep(wait_time)

        finally:
            if response is not None:
                response.close()

    return "FAILED", "", "Maximum retries exceeded"



# =========================
# SAVE DOWNLOAD LOG
# =========================

def save_log(results):

    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

    with open(LOG_FILE, "a", newline="", encoding="utf-8") as file:

        writer = csv.writer(file)

        if file.tell() == 0:
            writer.writerow([
                "arxiv_id",
                "status",
                "file_path",
                "file_size",
                "error"
            ])

        writer.writerows(results)


# =========================
# MAIN
# =========================

def main():

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    papers = load_papers()

    if MAX_PAPERS is not None:
        papers = papers[:MAX_PAPERS]

    print(f"Total papers to process: {len(papers)}")

    results = []

    for index, paper in enumerate(papers, start=1):

        paper_id = paper["id"]

        print(f"[{index}/{len(papers)}] Downloading {paper_id}")

        status, file_path, size_or_error = download_source(paper_id)

        if status in ("SUCCESS", "SKIPPED"):
            file_size = size_or_error
            error = ""
        else:
            file_size = 0
            error = str(size_or_error)

        results.append([
            paper_id,
            status,
            file_path,
            file_size,
            error
        ])

        print(f"    Status: {status}")

        time.sleep(DELAY_SECONDS)

    save_log(results)

    print("\nDownload completed!")
    print(f"Log saved to: {LOG_FILE}")


if __name__ == "__main__":
    main()
