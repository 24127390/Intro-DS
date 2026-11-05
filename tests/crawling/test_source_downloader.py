"""Unit tests for source_downloader module."""

import io
from pathlib import Path
import tarfile
from unittest.mock import MagicMock, patch

from src.crawling.source_downloader import (
    _extract_tex_from_tar,
    _unpack_archive_bytes,
    download_paper_version,
    download_sources_for_corpus,
    download_sources_for_paper,
)


def test_extract_tex_only_from_tar(tmp_path: Path) -> None:
    """Ensure that only .tex files are extracted, discarding image and binary files."""
    dest = tmp_path / "extracted"
    dest.mkdir()

    tar_bytes = io.BytesIO()
    with tarfile.open(fileobj=tar_bytes, mode="w:gz") as tar:
        # Add a .tex file
        tex_data = b"\\documentclass{article}\n\\begin{document}Hello\\end{document}"
        ti_tex = tarfile.TarInfo(name="main.tex")
        ti_tex.size = len(tex_data)
        tar.addfile(ti_tex, io.BytesIO(tex_data))

        # Add a nested .tex file
        sub_tex_data = b"\\section{Methods}\nContent"
        ti_sub = tarfile.TarInfo(name="sections/methods.tex")
        ti_sub.size = len(sub_tex_data)
        tar.addfile(ti_sub, io.BytesIO(sub_tex_data))

        # Add an image binary file (.png)
        img_data = b"\x89PNG\r\n\x1a\nfakeimagebytes"
        ti_img = tarfile.TarInfo(name="figures/plot.png")
        ti_img.size = len(img_data)
        tar.addfile(ti_img, io.BytesIO(img_data))

        # Add a compiled PDF (.pdf)
        pdf_data = b"%PDF-1.4 fakepdf"
        ti_pdf = tarfile.TarInfo(name="paper.pdf")
        ti_pdf.size = len(pdf_data)
        tar.addfile(ti_pdf, io.BytesIO(pdf_data))

    tar_bytes.seek(0)
    with tarfile.open(fileobj=tar_bytes, mode="r:gz") as tar:
        count = _extract_tex_from_tar(tar, dest)

    assert count == 2
    assert (dest / "main.tex").exists()
    assert (dest / "methods.tex").exists()
    assert not (dest / "plot.png").exists()
    assert not (dest / "paper.pdf").exists()


def test_extract_tex_and_bib_from_tar(tmp_path: Path) -> None:
    """Ensure that both .tex and .bib files are extracted, discarding binaries."""
    dest = tmp_path / "extracted_bib"
    dest.mkdir()

    tar_bytes = io.BytesIO()
    with tarfile.open(fileobj=tar_bytes, mode="w:gz") as tar:
        tex_data = b"\\documentclass{article}\\begin{document}\\cite{ref1}\\end{document}"
        ti_tex = tarfile.TarInfo(name="main.tex")
        ti_tex.size = len(tex_data)
        tar.addfile(ti_tex, io.BytesIO(tex_data))

        bib_data = b"@article{ref1, title={Sample}, author={Author}, year={2026}}"
        ti_bib = tarfile.TarInfo(name="refs.bib")
        ti_bib.size = len(bib_data)
        tar.addfile(ti_bib, io.BytesIO(bib_data))

        img_data = b"binary_image"
        ti_img = tarfile.TarInfo(name="fig.png")
        ti_img.size = len(img_data)
        tar.addfile(ti_img, io.BytesIO(img_data))

    tar_bytes.seek(0)
    with tarfile.open(fileobj=tar_bytes, mode="r:gz") as tar:
        count = _extract_tex_from_tar(tar, dest)

    assert count == 2
    assert (dest / "main.tex").exists()
    assert (dest / "refs.bib").exists()
    assert not (dest / "fig.png").exists()


def test_unpack_single_latex_text(tmp_path: Path) -> None:
    """Test unpacking plain text containing LaTeX markup."""
    dest = tmp_path / "plain"
    content = b"\\documentclass{article}\n\\begin{document}Simple\\end{document}"
    count = _unpack_archive_bytes(content, dest)
    assert count == 1
    assert (dest / "main.tex").exists()


def test_unpack_pdf_only_marker(tmp_path: Path) -> None:
    """Test that a non-LaTeX binary creates a .no_latex marker file."""
    dest = tmp_path / "pdf"
    content = b"%PDF-1.5 some binary stream not tar not gz"
    count = _unpack_archive_bytes(content, dest)
    assert count == 0
    assert (dest / ".no_latex").exists()


def test_download_paper_version_idempotent(tmp_path: Path) -> None:
    """Test that existing downloads are skipped."""
    v_dir = tmp_path / "2512.03231" / "v1"
    v_dir.mkdir(parents=True)
    (v_dir / "main.tex").write_text("\\documentclass{article}")

    # Should return True without making any network calls
    with patch("urllib.request.urlopen") as mock_open:
        res = download_paper_version(
            "2512.03231v1",
            "2512.03231",
            1,
            output_dir=tmp_path,
            delay=0.0,
        )
        assert res is True
        mock_open.assert_not_called()


def test_download_sources_for_corpus_progress(tmp_path: Path) -> None:
    """Test download_sources_for_corpus handles metadata, caching, and progress correctly."""
    import json

    paper_id = "2512.03231"
    paper_dir = tmp_path / paper_id
    paper_dir.mkdir(parents=True)

    # Write metadata
    meta = {"arxiv_id": paper_id, "versions": [f"{paper_id}v1"]}
    (paper_dir / "metadata.json").write_text(json.dumps(meta))

    # Pre-populate cached version
    v_dir = paper_dir / "v1"
    v_dir.mkdir()
    (v_dir / "main.tex").write_text("\\documentclass{article}")

    res = download_sources_for_corpus([paper_id, "missing_meta_id"], output_dir=tmp_path, delay=0.0)

    assert res["total_papers"] == 2
    assert res["completed_papers"] == 1
    assert res["cached_papers"] == 1
    assert res["failed_papers"] == 1

