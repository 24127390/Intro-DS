"""Download assigned arXiv sources and crawl Semantic Scholar references."""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import random
import re
import shutil
import tarfile
import tempfile
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path, PurePosixPath
from typing import Dict, Iterable, List, Optional, Tuple

import requests


ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / "config" / "aXirPaperID"
RAW_DIR = ROOT / "data" / "raw"
DISCOVERY_FILE = RAW_DIR / "arxiv_history_all_assignments.jsonl"
DISCOVERY_MANIFEST = RAW_DIR / "arxiv_discovery_status_all.csv"
SOURCE_DIR = RAW_DIR / "sources"
SOURCE_MANIFEST = RAW_DIR / "source_download_manifest.csv"
SEMANTIC_SCHOLAR_DIR = RAW_DIR / "semantic_scholar"
SEMANTIC_SCHOLAR_MANIFEST = RAW_DIR / "semantic_scholar_manifest.csv"
METADATA_FILE = RAW_DIR / "arxiv_metadata_all_assignments.jsonl"
METADATA_MANIFEST = RAW_DIR / "arxiv_metadata_manifest.csv"

REQUEST_TIMEOUT = 60
MAX_ATTEMPTS = 5
METADATA_BATCH_SIZE = 50
ARXIV_REQUEST_INTERVAL = 3.1
SEMANTIC_SCHOLAR_REQUEST_INTERVAL = 3.1
RETRYABLE_STATUSES = {429, 500, 502, 503, 504}
VERSION_PATTERN = re.compile(r"^(?P<base>.+)v(?P<version>\d+)$")


def assigned_base_ids(config_path: Path = CONFIG_PATH) -> List[str]:
    """Return unique base IDs listed in the group's assignment CSV."""
    identifiers = []
    with Path(config_path).open("r", encoding="utf-8-sig", newline="") as file:
        for row in csv.DictReader(file):
            start_month = row["start_month"].replace("-", "")
            end_month = row["end_month"].replace("-", "")
            if start_month != end_month:
                raise ValueError(
                    f"Assignment crosses months and needs explicit handling: {row}"
                )
            prefix = start_month[2:]
            start_id, end_id = int(row["start_id"]), int(row["end_id"])
            if end_id < start_id:
                raise ValueError(f"Invalid ID range in assignment: {row}")
            identifiers.extend(
                f"{prefix}.{number:05d}"
                for number in range(start_id, end_id + 1)
            )
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("The assignment file contains overlapping ID ranges.")
    return identifiers


def _read_jsonl(path: Path) -> List[dict]:
    records = []
    if path.exists():
        with path.open("r", encoding="utf-8") as file:
            for line_number, line in enumerate(file, start=1):
                if not line.strip():
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError as error:
                    raise ValueError(
                        f"Invalid JSON in {path} at line {line_number}"
                    ) from error
    return records


def _append_manifest(path: Path, fields: List[str], row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    new_file = not path.exists()
    with path.open("a", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        if new_file:
            writer.writeheader()
        writer.writerow(row)
        file.flush()
        os.fsync(file.fileno())


def _load_latest_status(path: Path, key_fields: Tuple[str, ...]) -> Dict[tuple, dict]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8", newline="") as file:
        rows = csv.DictReader(file)
        latest = {}
        for row in rows:
            latest[tuple(row[field] for field in key_fields)] = row
        return latest


def _append_jsonl(path: Path, records: Iterable[dict]) -> None:
    records = list(records)
    if not records:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as file:
        for record in records:
            file.write(json.dumps(record, ensure_ascii=False) + "\n")
        file.flush()
        os.fsync(file.fileno())


class RequestPacer:
    """Enforce a minimum interval between requests made by this process."""

    def __init__(self, interval: float):
        self.interval = interval
        self.last_request = None

    def wait(self) -> None:
        if self.last_request is not None:
            remaining = self.interval - (time.monotonic() - self.last_request)
            if remaining > 0:
                time.sleep(remaining)
        self.last_request = time.monotonic()


def _retry_after_seconds(response: requests.Response) -> Optional[float]:
    value = response.headers.get("Retry-After")
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(value)
            return max(0.0, (retry_at - datetime.now(timezone.utc)).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return None


def _get_with_retries(
    session: requests.Session,
    url: str,
    params: Optional[dict],
    pacer: RequestPacer,
) -> requests.Response:
    last_error = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        pacer.wait()
        try:
            response = session.get(
                url, params=params, timeout=REQUEST_TIMEOUT, stream=True
            )
            if response.status_code not in RETRYABLE_STATUSES:
                return response
            retry_after = _retry_after_seconds(response)
            response.close()
            if attempt == MAX_ATTEMPTS:
                response.raise_for_status()
            delay = retry_after
            if delay is None:
                delay = min(2 ** (attempt - 1) + random.random(), 60)
            time.sleep(delay)
        except requests.RequestException as error:
            last_error = error
            if attempt == MAX_ATTEMPTS:
                raise
            time.sleep(min(2 ** (attempt - 1) + random.random(), 60))
    raise last_error or RuntimeError(f"Request failed: {url}")


def _load_histories(base_ids: Iterable[str]) -> Dict[str, List[str]]:
    wanted = set(base_ids)
    histories: Dict[str, List[str]] = {}
    paths = [
        path for path in sorted(RAW_DIR.rglob("arxiv_history_*.jsonl"))
        if path != DISCOVERY_FILE
    ]
    if DISCOVERY_FILE.exists():
        paths.append(DISCOVERY_FILE)
    for path in paths:
        for record in _read_jsonl(path):
            base_id = record.get("base_id")
            versions = record.get("versions")
            if base_id in wanted and isinstance(versions, list):
                histories[base_id] = versions
    return histories


def _load_discovery_status() -> Dict[str, dict]:
    latest = _load_latest_status(DISCOVERY_MANIFEST, ("base_id",))
    return {key[0]: row for key, row in latest.items()}


def _write_history(
    base_id: str, versions: List[str], existing: set
) -> None:
    if base_id not in existing:
        with DISCOVERY_FILE.open("a", encoding="utf-8") as file:
            file.write(json.dumps(
                {"base_id": base_id, "versions": versions}, ensure_ascii=False
            ) + "\n")
            file.flush()
            os.fsync(file.fileno())
        existing.add(base_id)


def discover_assigned_versions(
    base_ids: Iterable[str],
    session: Optional[requests.Session] = None,
    interval: float = ARXIV_REQUEST_INTERVAL,
) -> Dict[str, List[str]]:
    """Discover versions for assigned IDs, reusing existing history checkpoints."""
    base_ids = list(base_ids)
    histories = _load_histories(base_ids)
    statuses = _load_discovery_status()
    canonical_ids = {
        record.get("base_id") for record in _read_jsonl(DISCOVERY_FILE)
    }
    http = session or requests.Session()
    http.headers.update({"User-Agent": "IntroDSSourceCrawler/1.0 (academic project)"})
    pacer = RequestPacer(interval)

    for index, base_id in enumerate(base_ids, start=1):
        if base_id in histories:
            continue
        if statuses.get(base_id, {}).get("status") == "not_found":
            continue
        started = time.perf_counter()
        error_message = ""
        status = "error"
        versions = []
        try:
            response = _get_with_retries(
                http, f"https://arxiv.org/abs/{base_id}", None, pacer
            )
            try:
                if response.status_code == 404:
                    status = "not_found"
                else:
                    response.raise_for_status()
                    from bs4 import BeautifulSoup

                    history = BeautifulSoup(
                        response.text, "html.parser"
                    ).select_one("div.submission-history")
                    if history is None:
                        raise ValueError("Submission history missing from arXiv page")
                    numbers = {
                        int(number)
                        for number in re.findall(
                            r"\bv(\d+)\b", history.get_text(" ", strip=True)
                        )
                    }
                    if not numbers:
                        raise ValueError("No version number found on arXiv page")
                    versions = [
                        f"{base_id}v{number}"
                        for number in range(1, max(numbers) + 1)
                    ]
                    _write_history(base_id, versions, canonical_ids)
                    histories[base_id] = versions
                    status = "success"
            finally:
                response.close()
        except (requests.RequestException, ValueError) as error:
            error_message = str(error)

        _append_manifest(
            DISCOVERY_MANIFEST,
            ["base_id", "status", "version_count", "elapsed_seconds", "error"],
            {
                "base_id": base_id,
                "status": status,
                "version_count": len(versions),
                "elapsed_seconds": round(time.perf_counter() - started, 3),
                "error": error_message,
            },
        )
        print(f"Discovery {index}/{len(base_ids)}: {base_id} [{status}]")
    return histories


METADATA_FIELDS = [
    "id", "title", "authors", "abstract", "published", "updated",
    "categories", "primary_category", "comment", "journal_ref", "doi", "pdf_url",
]
METADATA_MANIFEST_FIELDS = [
    "id", "status", "attempts", "elapsed_seconds", "record_source", "error",
]


def _metadata_record(paper) -> dict:
    entry_id = getattr(paper, "entry_id", None)
    if not entry_id:
        raise ValueError("arXiv API result did not include entry_id")
    return {
        "id": entry_id.rstrip("/").split("/")[-1],
        "title": paper.title,
        "authors": [author.name for author in paper.authors],
        "abstract": paper.summary,
        "published": paper.published.isoformat() if paper.published else None,
        "updated": paper.updated.isoformat() if paper.updated else None,
        "categories": paper.categories,
        "primary_category": paper.primary_category,
        "comment": paper.comment,
        "journal_ref": paper.journal_ref,
        "doi": paper.doi,
        "pdf_url": paper.pdf_url,
    }


def crawl_arxiv_metadata(
    histories: Dict[str, List[str]],
    session=None,
    output_path: Path = METADATA_FILE,
    manifest_path: Path = METADATA_MANIFEST,
    batch_size: int = METADATA_BATCH_SIZE,
    max_attempts: int = MAX_ATTEMPTS,
    search_factory=None,
) -> None:
    """Fetch metadata for all discovered versions into one resumable JSONL."""
    if batch_size < 1 or max_attempts < 1:
        raise ValueError("batch_size and max_attempts must be positive")

    import arxiv

    if session is None:
        session = arxiv.Client(
            page_size=min(batch_size, 100),
            delay_seconds=ARXIV_REQUEST_INTERVAL,
            num_retries=3,
        )
    if search_factory is None:
        search_factory = lambda identifiers: arxiv.Search(
            id_list=identifiers, max_results=len(identifiers)
        )

    expected_ids = {
        version_id
        for versions in histories.values()
        for version_id in versions
    }
    output_path, manifest_path = Path(output_path), Path(manifest_path)
    saved_records = {}
    if output_path.exists():
        for record in _read_jsonl(output_path):
            identifier = record.get("id")
            if identifier in expected_ids:
                saved_records[identifier] = record

    legacy_paths = sorted(RAW_DIR.rglob("arxiv_metadata_*_all_versions.jsonl"))
    legacy_records = []
    for path in legacy_paths:
        for record in _read_jsonl(path):
            identifier = record.get("id")
            if identifier in expected_ids and identifier not in saved_records:
                saved_records[identifier] = record
                legacy_records.append(record)
    _append_jsonl(output_path, legacy_records)

    manifest_latest = _load_latest_status(manifest_path, ("id",))
    for identifier, record in saved_records.items():
        if manifest_latest.get((identifier,), {}).get("status") == "success":
            continue
        _append_manifest(
            manifest_path,
            METADATA_MANIFEST_FIELDS,
            {
                "id": identifier,
                "status": "success",
                "attempts": 0,
                "elapsed_seconds": 0,
                "record_source": "existing_metadata_checkpoint",
                "error": "",
            },
        )

    pending_ids = sorted(expected_ids - set(saved_records))
    total_batches = (len(pending_ids) + batch_size - 1) // batch_size
    print(
        f"Metadata: {len(saved_records)} existing version records; "
        f"{len(pending_ids)} remaining across {total_batches} batches."
    )

    for batch_start in range(0, len(pending_ids), batch_size):
        batch = pending_ids[batch_start:batch_start + batch_size]
        received = {}
        errors = []
        attempts_by_id = {identifier: 0 for identifier in batch}
        elapsed_by_id = {identifier: 0.0 for identifier in batch}
        unresolved = set(batch)

        for attempt in range(1, max_attempts + 1):
            if not unresolved:
                break
            attempt_ids = sorted(unresolved)
            started = time.perf_counter()
            try:
                search = search_factory(attempt_ids)
                for paper in session.results(search):
                    record = _metadata_record(paper)
                    identifier = record["id"]
                    if identifier in unresolved:
                        received[identifier] = record
                        unresolved.remove(identifier)
            except Exception as error:
                errors.append(f"attempt {attempt}: {error}")
                print(
                    f"Metadata batch {batch_start // batch_size + 1}/"
                    f"{total_batches}, attempt {attempt}/{max_attempts} failed: {error}"
                )
            finally:
                attempt_elapsed = time.perf_counter() - started
                allocation = attempt_elapsed / len(attempt_ids)
                for identifier in attempt_ids:
                    attempts_by_id[identifier] = attempt
                    elapsed_by_id[identifier] += allocation

            if unresolved and attempt < max_attempts:
                time.sleep(min(2 ** (attempt - 1) + random.random(), 60))

        _append_jsonl(output_path, received.values())
        for identifier in batch:
            if identifier in received:
                row = {
                    "id": identifier,
                    "status": "success",
                    "attempts": attempts_by_id[identifier],
                    "elapsed_seconds": round(elapsed_by_id[identifier], 3),
                    "record_source": "arxiv_api",
                    "error": "",
                }
            else:
                row = {
                    "id": identifier,
                    "status": "error",
                    "attempts": attempts_by_id[identifier],
                    "elapsed_seconds": round(elapsed_by_id[identifier], 3),
                    "record_source": "",
                    "error": " | ".join(errors) or "Not returned by arXiv API",
                }
            _append_manifest(manifest_path, METADATA_MANIFEST_FIELDS, row)
        print(
            f"Metadata batch {batch_start // batch_size + 1}/{total_batches}: "
            f"{len(received)}/{len(batch)} records saved."
        )


def _safe_archive_member(member: tarfile.TarInfo) -> Optional[PurePosixPath]:
    if not member.isfile() or member.issym() or member.islnk():
        return None
    name = member.name.replace("\\", "/")
    path = PurePosixPath(name)
    if path.is_absolute() or not path.parts or ".." in path.parts:
        return None
    if ":" in path.parts[0]:
        return None
    if path.suffix.lower() not in {".tex", ".bib"}:
        return None
    return path


def extract_tex_bib_archive(
    archive: io.BufferedIOBase, destination: Path
) -> Tuple[int, int, int, int]:
    """Extract safe .tex/.bib files and return counts and before/after byte sizes."""
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    tex_count = bib_count = source_bytes_before_filter = source_bytes = 0
    try:
        tar = tarfile.open(fileobj=archive, mode="r:*")
    except tarfile.TarError as error:
        raise ValueError(f"Response is not a readable tar archive: {error}") from error
    try:
        with tar:
            seen_paths = set()
            for member in tar:
                if member.isfile():
                    source_bytes_before_filter += member.size
                relative_path = _safe_archive_member(member)
                if relative_path is None or relative_path in seen_paths:
                    continue
                seen_paths.add(relative_path)
                source_file = tar.extractfile(member)
                if source_file is None:
                    continue
                output_path = destination.joinpath(*relative_path.parts)
                output_path.parent.mkdir(parents=True, exist_ok=True)
                with source_file, output_path.open("wb") as target:
                    shutil.copyfileobj(source_file, target)
                source_bytes += member.size
                if relative_path.suffix.lower() == ".tex":
                    tex_count += 1
                else:
                    bib_count += 1
    except tarfile.TarError as error:
        raise ValueError(f"Invalid or unreadable source archive: {error}") from error
    return tex_count, bib_count, source_bytes_before_filter, source_bytes


SOURCE_FIELDS = [
    "arxiv_id", "version", "status", "archive_bytes",
    "source_bytes_before_filter", "source_bytes", "tex_files", "bib_files",
    "elapsed_seconds", "error",
]


def download_sources(
    histories: Dict[str, List[str]],
    session: Optional[requests.Session] = None,
    output_dir: Path = SOURCE_DIR,
    manifest_path: Path = SOURCE_MANIFEST,
    interval: float = ARXIV_REQUEST_INTERVAL,
) -> None:
    """Download each version's source archive and retain only .tex/.bib files."""
    output_dir, manifest_path = Path(output_dir), Path(manifest_path)
    latest = _load_latest_status(manifest_path, ("arxiv_id", "version"))
    http = session or requests.Session()
    http.headers.update({"User-Agent": "IntroDSSourceCrawler/1.0 (academic project)"})
    pacer = RequestPacer(interval)
    work = [
        (base_id, version_id)
        for base_id, version_ids in histories.items()
        for version_id in version_ids
    ]

    for index, (base_id, version_id) in enumerate(work, start=1):
        match = VERSION_PATTERN.fullmatch(version_id)
        if not match or match.group("base") != base_id:
            raise ValueError(f"Invalid version ID {version_id!r} for {base_id}")
        version = int(match.group("version"))
        key = (base_id, str(version))
        previous = latest.get(key, {})
        target = output_dir / base_id / f"v{version}"
        if previous.get("status") in {"success", "no_source", "no_tex_or_bib"}:
            has_source_files = target.exists() and any(
                path.is_file() and path.suffix.lower() in {".tex", ".bib"}
                for path in target.rglob("*")
            )
            if previous.get("status") != "success" or has_source_files:
                continue

        started = time.perf_counter()
        status, error_message = "error", ""
        archive_bytes = source_bytes_before_filter = source_bytes = 0
        tex_count = bib_count = 0
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and any(target.iterdir()):
            error_message = f"Output directory already contains untracked files: {target}"
        else:
            try:
                response = _get_with_retries(
                    http, f"https://export.arxiv.org/src/{version_id}", None, pacer
                )
                try:
                    if response.status_code == 404:
                        status = "no_source"
                        target.mkdir(exist_ok=True)
                    else:
                        response.raise_for_status()
                        with tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024) as archive:
                            for chunk in response.iter_content(chunk_size=1024 * 1024):
                                if chunk:
                                    archive.write(chunk)
                                    archive_bytes += len(chunk)
                            archive.seek(0)
                            with tempfile.TemporaryDirectory(
                                prefix=f".v{version}-", dir=str(target.parent)
                            ) as staging_dir:
                                stage = Path(staging_dir)
                                try:
                                    (
                                        tex_count,
                                        bib_count,
                                        source_bytes_before_filter,
                                        source_bytes,
                                    ) = (
                                        extract_tex_bib_archive(archive, stage)
                                    )
                                except ValueError as error:
                                    status = "non_archive"
                                    error_message = str(error)
                                    target.mkdir(exist_ok=True)
                                else:
                                    if tex_count or bib_count:
                                        if target.exists():
                                            if any(target.iterdir()):
                                                raise FileExistsError(
                                                    f"Output directory already contains "
                                                    f"untracked files: {target}"
                                                )
                                            target.rmdir()
                                        os.replace(stage, target)
                                        status = "success"
                                    else:
                                        status = "no_tex_or_bib"
                                        target.mkdir(exist_ok=True)
                finally:
                    response.close()
            except (requests.RequestException, OSError, ValueError) as error:
                error_message = str(error)

        row = {
            "arxiv_id": base_id,
            "version": version,
            "status": status,
            "archive_bytes": archive_bytes,
            "source_bytes_before_filter": source_bytes_before_filter,
            "source_bytes": source_bytes,
            "tex_files": tex_count,
            "bib_files": bib_count,
            "elapsed_seconds": round(time.perf_counter() - started, 3),
            "error": error_message,
        }
        _append_manifest(manifest_path, SOURCE_FIELDS, row)
        latest[key] = {field: str(value) for field, value in row.items()}
        print(
            f"Source {index}/{len(work)}: {version_id} [{status}] "
            f"({tex_count} .tex, {bib_count} .bib)"
        )


SEMANTIC_SCHOLAR_FIELDS = [
    "arxiv_id", "status", "reference_count", "arxiv_reference_count",
    "elapsed_seconds", "error",
]
REFERENCE_FIELDS = (
    "citedPaper.paperId,citedPaper.title,citedPaper.authors,citedPaper.year,"
    "citedPaper.publicationDate,citedPaper.venue,citedPaper.externalIds"
)
REFERENCE_PAGE_SIZE = 1000


def _fetch_all_semantic_scholar_references(
    base_id: str,
    session: requests.Session,
    pacer: RequestPacer,
    page_size: int = REFERENCE_PAGE_SIZE,
) -> List[dict]:
    if page_size < 1:
        raise ValueError("page_size must be positive")

    url = f"https://api.semanticscholar.org/graph/v1/paper/arXiv:{base_id}/references"
    references = []
    offset = 0
    while True:
        response = _get_with_retries(
            session,
            url,
            {"fields": REFERENCE_FIELDS, "offset": offset, "limit": page_size},
            pacer,
        )
        try:
            response.raise_for_status()
            payload = response.json()
        finally:
            response.close()

        page = payload.get("data")
        if not isinstance(page, list):
            raise ValueError("Semantic Scholar response is missing its data list")
        if not page:
            break
        for item in page:
            paper = item.get("citedPaper") if isinstance(item, dict) else None
            if not isinstance(paper, dict):
                raise ValueError("Semantic Scholar returned a malformed reference row")
            references.append(paper)
        if len(page) < page_size:
            break
        offset += len(page)
    return references


def _write_json_atomic(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=path.parent, delete=False
        ) as file:
            temp_path = Path(file.name)
            json.dump(record, file, ensure_ascii=False)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        os.replace(temp_path, path)
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink()


def crawl_semantic_scholar(
    base_ids: Iterable[str],
    session: Optional[requests.Session] = None,
    output_dir: Path = SEMANTIC_SCHOLAR_DIR,
    manifest_path: Path = SEMANTIC_SCHOLAR_MANIFEST,
    interval: float = SEMANTIC_SCHOLAR_REQUEST_INTERVAL,
) -> None:
    """Save all S2 reference records per paper, including non-arXiv references."""
    output_dir, manifest_path = Path(output_dir), Path(manifest_path)
    latest = _load_latest_status(manifest_path, ("arxiv_id",))
    http = session or requests.Session()
    http.headers.update({"User-Agent": "IntroDSReferenceCrawler/1.0 (academic project)"})
    api_key = os.environ.get("S2_API_KEY")
    if api_key:
        http.headers.update({"x-api-key": api_key})
    pacer = RequestPacer(interval)
    base_ids = list(base_ids)

    for index, base_id in enumerate(base_ids, start=1):
        previous = latest.get((base_id,), {})
        result_path = output_dir / f"{base_id}.json"
        if previous.get("status") in {"success", "not_found"} and result_path.exists():
            continue

        started = time.perf_counter()
        status, error_message, references = "error", "", []
        try:
            references = _fetch_all_semantic_scholar_references(
                base_id, http, pacer
            )
            _write_json_atomic(
                result_path,
                {"arxiv_id": base_id, "references": references},
            )
            status = "success"
        except requests.HTTPError as error:
            if error.response is not None and error.response.status_code == 404:
                status = "not_found"
                _write_json_atomic(
                    result_path,
                    {"arxiv_id": base_id, "status": status, "references": []},
                )
            else:
                error_message = str(error)
        except (requests.RequestException, ValueError, json.JSONDecodeError) as error:
            error_message = str(error)

        arxiv_reference_count = sum(
            bool((reference.get("externalIds") or {}).get("ArXiv"))
            for reference in references
            if isinstance(reference, dict)
        )
        row = {
            "arxiv_id": base_id,
            "status": status,
            "reference_count": len(references),
            "arxiv_reference_count": arxiv_reference_count,
            "elapsed_seconds": round(time.perf_counter() - started, 3),
            "error": error_message,
        }
        _append_manifest(manifest_path, SEMANTIC_SCHOLAR_FIELDS, row)
        latest[(base_id,)] = {field: str(value) for field, value in row.items()}
        print(
            f"Semantic Scholar {index}/{len(base_ids)}: {base_id} [{status}] "
            f"({len(references)} references)"
        )


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Discover versions, download arXiv sources, and crawl references."
    )
    parser.add_argument("--discover", action="store_true", help="discover missing version histories")
    parser.add_argument("--metadata", action="store_true", help="crawl metadata for every version")
    parser.add_argument("--source", action="store_true", help="download all version source archives")
    parser.add_argument("--semantic-scholar", action="store_true", help="crawl references once per paper")
    parser.add_argument("--limit", type=int, help="process only the first N assigned papers (for testing)")
    args = parser.parse_args(argv)
    if not (args.discover or args.metadata or args.source or args.semantic_scholar):
        parser.error(
            "select at least one of --discover, --metadata, --source, "
            "or --semantic-scholar"
        )
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be positive")

    base_ids = assigned_base_ids()
    if args.limit:
        base_ids = base_ids[:args.limit]

    histories = _load_histories(base_ids)
    if args.discover or args.metadata or args.source:
        histories = discover_assigned_versions(base_ids)
    if args.metadata:
        crawl_arxiv_metadata(histories)
    if args.source:
        download_sources(histories)
    if args.semantic_scholar:
        crawl_semantic_scholar(base_ids)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
