from pathlib import Path
from src.latexParser import LatexFileResolver, PublicationHierarchyBuilder


def test_multi_file_and_deduplication(tmp_path: Path):
    # Giả lập bài báo 2310-12345 với 2 version như trong đặc tả parsing.md
    v1_dir = tmp_path / "2310-12345" / "v1"
    v2_dir = tmp_path / "2310-12345" / "v2"
    v1_dir.mkdir(parents=True)
    v2_dir.mkdir(parents=True)

    # Version 1: tách làm main.tex + intro.tex + unused.tex (file thừa)
    (v1_dir / "main.tex").write_text(
        r"""
        \documentclass{article}
        \begin{document}
        % \input{unused.tex}
        \input{intro}
        \section*{References}
        \bibitem{ref1} Ignored reference section.
        \end{document}
        """,
        encoding="utf-8",
    )
    (v1_dir / "intro.tex").write_text(
        r"""
        \section{Introduction}
        \centering We study the citation matching problem.
        """,
        encoding="utf-8",
    )
    (v1_dir / "unused.tex").write_text(
        r"\section{Should Not Be Parsed} Secret text.", encoding="utf-8"
    )

    (v2_dir / "main.tex").write_text(
        r"""
        \documentclass{article}
        \begin{document}
        \section{Background and model}
        We study the citation matching problem.
        \end{document}
        """,
        encoding="utf-8",
    )

    resolver = LatexFileResolver()
    builder = PublicationHierarchyBuilder("2310.12345")

    res_v1 = resolver.resolve_version(v1_dir)
    assert not res_v1["is_empty"]
    assert "unused.tex" in res_v1["ignored_files"]
    builder.parse_version(1, res_v1["compiled_tex"])

    res_v2 = resolver.resolve_version(v2_dir)
    builder.parse_version(2, res_v2["compiled_tex"])

    sentences = [e for e in builder.elements_rows if e["kind"] == "sentence"]
    assert len(sentences) == 1
    assert sentences[0]["element_id"] == "2310-12345#s1"

    headings = [e["content"] for e in builder.elements_rows if e["kind"] == "heading"]
    assert "References" not in headings
    assert headings == ["Introduction", "Background and model"]