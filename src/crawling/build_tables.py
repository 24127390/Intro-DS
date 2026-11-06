"""Build Milestone 1 Parquet tables from the raw crawler outputs."""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import pyarrow as pa
import pyarrow.parquet as pq

from src.crawling.source_and_references import (
    DISCOVERY_FILE,
    DISCOVERY_MANIFEST,
    METADATA_FILE,
    METADATA_MANIFEST,
    RAW_DIR,
    ROOT,
    SEMANTIC_SCHOLAR_MANIFEST,
    SEMANTIC_SCHOLAR_DIR,
    SOURCE_DIR,
    SOURCE_MANIFEST,
    assigned_base_ids,
)


PROCESSED_DIR = ROOT / "data" / "processed"
PAPERS_PATH = PROCESSED_DIR / "papers.parquet"
REFERENCES_PATH = PROCESSED_DIR / "references.parquet"
CRAWL_MANIFEST_PATH = PROCESSED_DIR / "crawl_manifest.csv"
VERSION_SUFFIX = re.compile(r"v(\d+)$")

PAPERS_SCHEMA = pa.schema([
    ("arxiv_id", pa.string()),
    ("title", pa.string()),
    ("authors", pa.list_(pa.string())),
    ("submission_date", pa.string()),
    ("revised_dates", pa.list_(pa.string())),
    ("venue", pa.string()),
])
REFERENCES_SCHEMA = pa.schema([
    ("arxiv_id", pa.string()),
    ("ref_arxiv_id", pa.string()),
    ("ref_title", pa.string()),
    ("ref_authors", pa.list_(pa.string())),
    ("ref_submission_date", pa.string()),
    ("ref_s2_id", pa.string()),
])


def _read_jsonl(path: Path) -> Iterable[dict]:
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as file:
        for line_number, line in enumerate(file, start=1):
            if not line.strip():
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(f"Invalid JSON at {path}:{line_number}") from error


def _date(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    return str(value)[:10]


def _base_and_version(identifier: str) -> tuple[str, int]:
    match = VERSION_SUFFIX.search(identifier)
    if not match:
        return identifier, 1
    return identifier[:match.start()], int(match.group(1))


def _paper_rows(
    base_ids: Iterable[str], metadata_path: Path
) -> List[dict]:
    by_base: Dict[str, List[dict]] = defaultdict(list)
    for record in _read_jsonl(metadata_path):
        identifier = record.get("id")
        if not identifier:
            continue
        base_id, version = _base_and_version(identifier)
        by_base[base_id].append((version, record))

    rows = []
    for base_id in base_ids:
        versions = sorted(by_base.get(base_id, []), key=lambda item: item[0])
        if not versions:
            continue
        records = [record for _, record in versions]
        submission_dates = sorted({
            date for record in records
            if (date := _date(record.get("published"))) is not None
        })
        submission_date = submission_dates[0] if submission_dates else None
        revised_dates = sorted({
            date for record in records
            if (date := _date(record.get("updated"))) is not None
            and date != submission_date
        })
        latest = records[-1] if records else {}
        venue = next(
            (record.get("journal_ref") for record in reversed(records)
             if record.get("journal_ref")),
            None,
        )
        authors = latest.get("authors") or []
        rows.append({
            "arxiv_id": base_id,
            "title": latest.get("title"),
            "authors": [str(author) for author in authors],
            "submission_date": submission_date,
            "revised_dates": revised_dates,
            "venue": venue,
        })
    return rows


def _reference_rows(reference_dir: Path) -> List[dict]:
    rows = []
    for path in sorted(Path(reference_dir).glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise ValueError(f"Invalid Semantic Scholar JSON: {path}") from error
        citing_id = payload.get("arxiv_id", path.stem)
        for reference in payload.get("references") or []:
            if not isinstance(reference, dict):
                raise ValueError(f"Invalid reference record in {path}")
            external_ids = reference.get("externalIds") or {}
            ref_arxiv_id = external_ids.get("ArXiv")
            if not ref_arxiv_id:
                continue
            authors = reference.get("authors") or []
            rows.append({
                "arxiv_id": citing_id,
                "ref_arxiv_id": str(ref_arxiv_id),
                "ref_title": reference.get("title"),
                "ref_authors": [
                    str(author.get("name", ""))
                    if isinstance(author, dict) else str(author)
                    for author in authors
                ],
                "ref_submission_date": _date(reference.get("publicationDate")),
                "ref_s2_id": reference.get("paperId"),
            })
    return rows


CRAWL_MANIFEST_FIELDS = [
    "arxiv_id", "version", "discovery_status", "metadata_status",
    "source_status", "tex_or_bib_found", "semantic_scholar_status",
    "reference_count", "arxiv_reference_count", "discovery_elapsed_seconds",
    "metadata_elapsed_seconds", "source_elapsed_seconds",
    "semantic_scholar_elapsed_seconds", "error",
]


def _read_csv_latest(path: Path, keys: List[str]) -> Dict[tuple, dict]:
    latest = {}
    if path.exists():
        with path.open("r", encoding="utf-8", newline="") as file:
            for row in csv.DictReader(file):
                latest[tuple(row.get(key, "") for key in keys)] = row
    return latest


def _history_records(base_ids: Iterable[str]) -> Dict[str, List[str]]:
    wanted = set(base_ids)
    paths = [
        path for path in sorted(RAW_DIR.rglob("arxiv_history_*.jsonl"))
        if path != DISCOVERY_FILE
    ]
    if DISCOVERY_FILE.exists():
        paths.append(DISCOVERY_FILE)
    histories = {}
    for path in paths:
        for record in _read_jsonl(path):
            if record.get("base_id") in wanted:
                histories[record["base_id"]] = record.get("versions") or []
    return histories


def _write_csv_atomic(rows: List[dict], fields: List[str], path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", newline="", prefix=f".{path.stem}-",
            suffix=".csv", dir=path.parent, delete=False,
        ) as file:
            temp_path = Path(file.name)
            writer = csv.DictWriter(file, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temp_path, path)
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink()


def _crawl_manifest_rows(
    base_ids: Iterable[str], metadata_path: Path
) -> List[dict]:
    histories = _history_records(base_ids)
    discovery = _read_csv_latest(DISCOVERY_MANIFEST, ["base_id"])
    metadata = _read_csv_latest(METADATA_MANIFEST, ["id"])
    source = _read_csv_latest(SOURCE_MANIFEST, ["arxiv_id", "version"])
    semantic = _read_csv_latest(SEMANTIC_SCHOLAR_MANIFEST, ["arxiv_id"])

    metadata_records = {}
    metadata_versions: Dict[str, List[str]] = defaultdict(list)
    for record in _read_jsonl(metadata_path):
        identifier = record.get("id")
        if identifier:
            metadata_records[identifier] = record
            base_id, _ = _base_and_version(identifier)
            metadata_versions[base_id].append(identifier)

    rows = []
    for base_id in base_ids:
        versions = histories.get(base_id, [])
        if not versions:
            versions = sorted(
                set(metadata_versions.get(base_id, [])),
                key=lambda identifier: _base_and_version(identifier)[1],
            )
        discovery_row = discovery.get((base_id,), {})
        discovery_status = discovery_row.get(
            "status",
            "success" if base_id in histories or versions else "not_attempted",
        )
        semantic_row = semantic.get((base_id,), {})
        version_ids = versions or [""]
        for version_id in version_ids:
            version = VERSION_SUFFIX.search(version_id).group(1) if version_id else ""
            metadata_row = metadata.get((version_id,), {})
            source_row = source.get((base_id, version), {})
            if version_id in metadata_records:
                metadata_ok = "success"
            elif metadata_row.get("status") == "success":
                metadata_ok = "missing_output"
            else:
                metadata_ok = metadata_row.get("status", "not_attempted")
            source_status = source_row.get("status", "not_attempted" if version else "")
            if source_status == "success":
                version_dir = Path(SOURCE_DIR) / base_id / f"v{version}"
                source_files_found = version_dir.exists() and any(
                    path.is_file() and path.suffix.lower() in {".tex", ".bib"}
                    for path in version_dir.rglob("*")
                )
                if not source_files_found:
                    source_status = "missing_output"
            semantic_status = semantic_row.get("status", "not_attempted")
            semantic_path = Path(SEMANTIC_SCHOLAR_DIR) / f"{base_id}.json"
            if semantic_status in {"success", "not_found"} and not semantic_path.exists():
                semantic_status = "missing_output"
            tex_or_bib_found = (
                str(
                    int(source_row.get("tex_files") or 0)
                    + int(source_row.get("bib_files") or 0) > 0
                ).lower()
                if source_row else ""
            )
            errors = [
                value for value in (
                    discovery_row.get("error"),
                    metadata_row.get("error"),
                    source_row.get("error"),
                    semantic_row.get("error"),
                ) if value
            ]
            if metadata_ok == "missing_output":
                errors.append("Metadata manifest says success but JSONL record is missing")
            if source_status == "missing_output":
                errors.append("Source manifest says success but .tex/.bib files are missing")
            if semantic_status == "missing_output":
                errors.append("Semantic Scholar manifest says success but JSON file is missing")
            rows.append({
                "arxiv_id": base_id,
                "version": version,
                "discovery_status": discovery_status,
                "metadata_status": metadata_ok,
                "source_status": source_status,
                "tex_or_bib_found": tex_or_bib_found,
                "semantic_scholar_status": semantic_status,
                "reference_count": semantic_row.get("reference_count", ""),
                "arxiv_reference_count": semantic_row.get("arxiv_reference_count", ""),
                "discovery_elapsed_seconds": discovery_row.get("elapsed_seconds", ""),
                "metadata_elapsed_seconds": metadata_row.get("elapsed_seconds", ""),
                "source_elapsed_seconds": source_row.get("elapsed_seconds", ""),
                "semantic_scholar_elapsed_seconds": semantic_row.get("elapsed_seconds", ""),
                "error": " | ".join(errors),
            })
    return rows


def _write_parquet_atomic(rows: List[dict], schema: pa.Schema, path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix=f".{path.stem}-", suffix=".parquet",
            dir=path.parent, delete=False,
        ) as file:
            temp_path = Path(file.name)
        table = pa.Table.from_pylist(rows, schema=schema)
        pq.write_table(table, temp_path)
        os.replace(temp_path, path)
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink()


def build_tables(
    config_path: Path,
    metadata_path: Path,
    reference_dir: Path,
    papers_path: Path,
    references_path: Path,
    crawl_manifest_path: Optional[Path] = None,
) -> tuple[int, int, int]:
    base_ids = assigned_base_ids(config_path)
    paper_rows = _paper_rows(base_ids, metadata_path)
    reference_rows = _reference_rows(reference_dir)
    _write_parquet_atomic(paper_rows, PAPERS_SCHEMA, papers_path)
    _write_parquet_atomic(reference_rows, REFERENCES_SCHEMA, references_path)
    manifest_rows = _crawl_manifest_rows(base_ids, metadata_path)
    if crawl_manifest_path is not None:
        _write_csv_atomic(manifest_rows, CRAWL_MANIFEST_FIELDS, crawl_manifest_path)
    return len(paper_rows), len(reference_rows), len(manifest_rows)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build Milestone 1 papers and references Parquet tables."
    )
    parser.add_argument("--config", type=Path, default=ROOT / "config" / "aXirPaperID")
    parser.add_argument("--metadata", type=Path, default=METADATA_FILE)
    parser.add_argument("--semantic-scholar-dir", type=Path, default=SEMANTIC_SCHOLAR_DIR)
    parser.add_argument("--papers-output", type=Path, default=PAPERS_PATH)
    parser.add_argument("--references-output", type=Path, default=REFERENCES_PATH)
    parser.add_argument("--manifest-output", type=Path, default=CRAWL_MANIFEST_PATH)
    args = parser.parse_args(argv)

    papers, references, manifest_rows = build_tables(
        args.config,
        args.metadata,
        args.semantic_scholar_dir,
        args.papers_output,
        args.references_output,
        args.manifest_output,
    )
    print(f"papers: {papers:,} rows -> {args.papers_output}")
    print(f"references: {references:,} rows -> {args.references_output}")
    print(f"crawl manifest: {manifest_rows:,} rows -> {args.manifest_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
