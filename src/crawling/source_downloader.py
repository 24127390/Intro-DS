"""Source downloader module for arXiv LaTeX sources.

Downloads full-text source archives from the dedicated export mirror and fallback endpoints,
extracting strictly .tex and .bib files while discarding binary figures.
"""

import gzip
import io
import json
from pathlib import Path
import shutil
import tarfile
import time
from typing import Any, Dict, List, Optional

import requests

EXPORT_SRC_BASE_URL = "https://export.arxiv.org/src"
FALLBACK_SRC_BASE_URL = "https://arxiv.org/src"
EPRINT_BASE_URL = "https://arxiv.org/e-print"
SOURCE_RATE_LIMIT_DELAY = 3.0
USER_AGENT = "IntroDS-Crawler/1.0 (HCMUS Group 13; 24127559@student.hcmus.edu.vn)"


def _is_tex_or_bib_filename(name: str) -> bool:
    """Check if filename ends with .tex or .bib extension (case-insensitive)."""
    return name.lower().endswith((".tex", ".bib"))


# Backward-compatible alias
_is_tex_filename = _is_tex_or_bib_filename


def _extract_tex_from_tar(tar: tarfile.TarFile, dest_dir: Path) -> int:
    """Extract only .tex and .bib members from a tar archive and record size statistics.

    Args:
        tar: Open TarFile object.
        dest_dir: Destination directory for extracted source files.

    Returns:
        Number of .tex and .bib files extracted.
    """
    extracted = 0
    raw_uncompressed_bytes = sum(m.size for m in tar.getmembers() if m.isfile())

    for member in tar.getmembers():
        if member.isfile() and _is_tex_or_bib_filename(member.name):
            # Guard against directory traversal attacks in archive
            target_path = dest_dir / Path(member.name).name
            fileobj = tar.extractfile(member)
            if fileobj:
                with target_path.open("wb") as out_f:
                    shutil.copyfileobj(fileobj, out_f)
                extracted += 1

    clean_extracted_bytes = sum(
        f.stat().st_size
        for f in dest_dir.glob("*")
        if f.is_file() and not f.name.startswith(".")
    )

    stats_data = {
        "raw_uncompressed_bytes": raw_uncompressed_bytes,
        "clean_extracted_bytes": clean_extracted_bytes,
        "files_extracted": extracted,
    }
    with (dest_dir / ".size_stats.json").open("w", encoding="utf-8") as sf:
        json.dump(stats_data, sf, indent=2)

    return extracted


def _unpack_archive_bytes(data: bytes, dest_dir: Path) -> int:
    """Inspect and unpack raw bytes from arXiv source endpoint.

    Args:
        data: Raw downloaded bytes.
        dest_dir: Target version directory.

    Returns:
        Number of .tex and .bib files extracted.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)

    # 1. Check if it is a tar (or tar.gz) archive
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as tar:
            return _extract_tex_from_tar(tar, dest_dir)
    except tarfile.TarError:
        pass

    # 2. Check if it is a single gzipped .tex or .bib file
    try:
        decompressed = gzip.decompress(data)
        # Verify if decompressed content looks like text
        try:
            text = decompressed.decode("utf-8", errors="replace")
            if "\\documentclass" in text or "\\begin" in text or "%" in text or "@" in text:
                target_path = dest_dir / "main.tex"
                with target_path.open("w", encoding="utf-8") as out_f:
                    out_f.write(text)
                stats_data = {
                    "raw_uncompressed_bytes": len(decompressed),
                    "clean_extracted_bytes": target_path.stat().st_size,
                    "files_extracted": 1,
                }
                with (dest_dir / ".size_stats.json").open("w", encoding="utf-8") as sf:
                    json.dump(stats_data, sf, indent=2)
                return 1
        except Exception:
            pass
    except Exception:
        pass

    # 3. If raw bytes are plain LaTeX text
    try:
        text = data.decode("utf-8", errors="replace")
        if "\\documentclass" in text or "\\begin" in text or "@" in text:
            target_path = dest_dir / "main.tex"
            with target_path.open("w", encoding="utf-8") as out_f:
                out_f.write(text)
            stats_data = {
                "raw_uncompressed_bytes": len(data),
                "clean_extracted_bytes": target_path.stat().st_size,
                "files_extracted": 1,
            }
            with (dest_dir / ".size_stats.json").open("w", encoding="utf-8") as sf:
                json.dump(stats_data, sf, indent=2)
            return 1
    except Exception:
        pass

    # Non-LaTeX source (e.g. PDF-only or unsupported format)
    (dest_dir / ".no_latex").touch()
    stats_data = {
        "raw_uncompressed_bytes": len(data),
        "clean_extracted_bytes": 0,
        "files_extracted": 0,
    }
    with (dest_dir / ".size_stats.json").open("w", encoding="utf-8") as sf:
        json.dump(stats_data, sf, indent=2)
    return 0


def download_paper_version(
    version_id: str,
    base_id: str,
    version_num: int,
    output_dir: Path = Path("data/raw/corpus"),
    delay: float = SOURCE_RATE_LIMIT_DELAY,
) -> bool:
    """Download and unpack a single version source archive.

    Tries the export mirror first (export.arxiv.org/src), falling back to main
    endpoints if the mirror response is truncated or rate-limited.

    Args:
        version_id: Suffix version ID, e.g. '2512.03231v1'.
        base_id: Base paper ID, e.g. '2512.03231'.
        version_num: Integer version number, e.g. 1.
        output_dir: Corpus root directory.
        delay: Delay in seconds to respect rate limits.

    Returns:
        True if successfully processed (or already cached), False on error.
    """
    version_dir = output_dir / base_id / f"v{version_num}"
    if version_dir.exists():
        # Check if already downloaded
        has_sources = any(version_dir.glob("*.tex")) or any(version_dir.glob("*.bib"))
        if has_sources or (version_dir / ".no_latex").exists():
            return True

    candidate_urls = [
        f"{EXPORT_SRC_BASE_URL}/{version_id}",
        f"{FALLBACK_SRC_BASE_URL}/{version_id}",
        f"{EPRINT_BASE_URL}/{version_id}",
    ]

    headers = {"User-Agent": USER_AGENT}

    for url in candidate_urls:
        max_retries = 3
        for attempt in range(max_retries):
            try:
                time.sleep(delay)
                resp = requests.get(url, headers=headers, timeout=45)
                if resp.status_code == 200:
                    _unpack_archive_bytes(resp.content, version_dir)
                    return True
                elif resp.status_code in (404, 403):
                    if url == candidate_urls[-1]:
                        version_dir.mkdir(parents=True, exist_ok=True)
                        (version_dir / ".no_latex").touch()
                        return True
                    break
                elif resp.status_code in (429, 503):
                    time.sleep(delay * (2 ** (attempt + 1)))
                    continue
                else:
                    break
            except Exception:
                time.sleep(delay * 2)
                continue

    return False


def download_sources_for_paper(
    base_id: str,
    output_dir: Path = Path("data/raw/corpus"),
    delay: float = SOURCE_RATE_LIMIT_DELAY,
) -> Dict[str, Any]:
    """Download sources for all versions of a single paper.

    Args:
        base_id: Base arXiv ID.
        output_dir: Corpus root directory.
        delay: Inter-request pacing delay.

    Returns:
        Summary dict of versions attempted and succeeded.
    """
    paper_dir = output_dir / base_id
    meta_path = paper_dir / "metadata.json"

    if not meta_path.exists():
        return {"base_id": base_id, "status": "no_metadata"}

    with meta_path.open("r", encoding="utf-8") as f:
        meta = json.load(f)

    versions: List[str] = meta.get("versions", [f"{base_id}v1"])
    success_count = 0

    for idx, v_id in enumerate(versions, start=1):
        ok = download_paper_version(
            version_id=v_id,
            base_id=base_id,
            version_num=idx,
            output_dir=output_dir,
            delay=delay,
        )
        if ok:
            success_count += 1

    return {
        "base_id": base_id,
        "total_versions": len(versions),
        "downloaded_versions": success_count,
    }


def download_sources_for_corpus(
    arxiv_ids: List[str],
    output_dir: Path = Path("data/raw/corpus"),
    delay: float = SOURCE_RATE_LIMIT_DELAY,
) -> Dict[str, Any]:
    """Download sources for all assigned papers and their versions.

    Args:
        arxiv_ids: List of base arXiv IDs.
        output_dir: Corpus root directory.
        delay: Inter-request pacing delay.

    Returns:
        Summary dictionary of download statistics.
    """
    total = len(arxiv_ids)
    completed = 0

    for aid in arxiv_ids:
        res = download_sources_for_paper(aid, output_dir=output_dir, delay=delay)
        if res.get("status") != "no_metadata":
            completed += 1

    return {
        "total_papers": total,
        "completed_papers": completed,
    }
