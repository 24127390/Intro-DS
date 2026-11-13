import csv
import io
import json
import sys
import tarfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import src.crawling.source_and_references as crawler
import src.crawling.build_tables as table_builder
from src.crawling.source_and_references import (
    assigned_base_ids,
    crawl_arxiv_metadata,
    crawl_semantic_scholar,
    download_sources,
    extract_tex_bib_archive,
    _fetch_all_semantic_scholar_references,
    RequestPacer,
)
from src.crawling.build_tables import build_tables


def _archive_bytes(files):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, content in files.items():
            data = content.encode("utf-8")
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


class _Response:
    def __init__(self, status_code=200, payload=None, body=b""):
        self.status_code = status_code
        self.headers = {}
        self._payload = payload
        self._body = body

    @property
    def content(self):
        return self._body

    @property
    def text(self):
        return self._body.decode("utf-8")

    def iter_content(self, chunk_size):
        yield self._body

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def close(self):
        pass


class _Session:
    def __init__(self, response):
        self.headers = {}
        self.responses = response if isinstance(response, list) else [response]
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)


class _MetadataClient:
    def __init__(self, papers):
        self.papers = papers
        self.calls = []

    def results(self, search):
        self.calls.append(search)
        return iter(self.papers)


class _UnusedMetadataClient:
    def results(self, search):
        raise AssertionError("Existing metadata should be reused")


def test_assignment_config_contains_25000_unique_ids():
    ids = assigned_base_ids(Path(__file__).parents[1] / "config" / "aXirPaperID")
    assert len(ids) == 25_000
    assert len(set(ids)) == 25_000
    assert "2602.19869" in ids


def test_metadata_crawl_writes_combined_records_and_status(tmp_path, monkeypatch):
    identifier = "2603.99999v1"
    paper = SimpleNamespace(
        entry_id=f"https://arxiv.org/abs/{identifier}",
        title="Example",
        authors=[SimpleNamespace(name="Author")],
        summary="Abstract",
        published=datetime(2026, 3, 1, tzinfo=timezone.utc),
        updated=datetime(2026, 3, 2, tzinfo=timezone.utc),
        categories=["cs.AI"],
        primary_category="cs.AI",
        comment=None,
        journal_ref=None,
        doi=None,
        pdf_url=f"https://arxiv.org/pdf/{identifier}",
    )
    client = _MetadataClient([paper])
    monkeypatch.setitem(
        sys.modules, "arxiv", SimpleNamespace(Client=lambda **kwargs: client)
    )
    output_path = tmp_path / "metadata.jsonl"
    manifest_path = tmp_path / "metadata_manifest.csv"

    crawl_arxiv_metadata(
        {"2603.99999": [identifier]},
        session=client,
        output_path=output_path,
        manifest_path=manifest_path,
        batch_size=1,
        search_factory=lambda ids: ids,
    )

    record = json.loads(output_path.read_text(encoding="utf-8").strip())
    assert record["id"] == identifier
    assert record["authors"] == ["Author"]
    with manifest_path.open(encoding="utf-8", newline="") as file:
        row = next(csv.DictReader(file))
    assert row["id"] == identifier
    assert row["status"] == "success"
    assert client.calls == [[identifier]]


def test_metadata_crawl_reuses_legacy_records_in_combined_output(tmp_path, monkeypatch):
    identifier = "2602.14870v1"
    member_dir = tmp_path / "Nam"
    member_dir.mkdir()
    legacy = member_dir / "arxiv_metadata_2602_all_versions.jsonl"
    record = {"id": identifier, "title": "Existing"}
    legacy.write_text(json.dumps(record) + "\n", encoding="utf-8")
    monkeypatch.setattr(crawler, "RAW_DIR", tmp_path)
    monkeypatch.setitem(sys.modules, "arxiv", SimpleNamespace())
    output_path = tmp_path / "arxiv_metadata_all_assignments.jsonl"

    crawl_arxiv_metadata(
        {"2602.14870": [identifier]},
        session=_UnusedMetadataClient(),
        output_path=output_path,
        manifest_path=tmp_path / "metadata_manifest.csv",
        search_factory=lambda ids: ids,
    )

    assert json.loads(output_path.read_text(encoding="utf-8")) == record
    with (tmp_path / "metadata_manifest.csv").open(
        encoding="utf-8", newline=""
    ) as file:
        row = next(csv.DictReader(file))
    assert row["status"] == "success"
    assert row["record_source"] == "existing_metadata_checkpoint"


def test_version_discovery_reuses_nested_member_history(tmp_path, monkeypatch):
    member_dir = tmp_path / "Nam"
    member_dir.mkdir()
    (member_dir / "arxiv_history_2602.jsonl").write_text(
        json.dumps({
            "base_id": "2602.14870",
            "versions": ["2602.14870v1"],
        }) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(crawler, "RAW_DIR", tmp_path)

    histories = crawler._load_histories(["2602.14870"])

    assert histories == {"2602.14870": ["2602.14870v1"]}


def test_version_discovery_batches_api_results_and_falls_back_for_missing_ids(
    tmp_path, monkeypatch
):
    base_ids = [f"2603.{number:05d}" for number in range(101)]
    entries = "".join(
        f"<entry><id>https://arxiv.org/abs/{base_id}v"
        f"{3 if index == 0 else 1}</id></entry>"
        for index, base_id in enumerate(base_ids[:100])
    )
    api_response = (
        f'<feed xmlns="http://www.w3.org/2005/Atom">{entries}</feed>'
    ).encode()
    empty_api_response = (
        b'<feed xmlns="http://www.w3.org/2005/Atom"></feed>'
    )
    fallback_html = (
        b'<div class="submission-history"> '
        b'Submitted on 1 Mar 2026 (v1), 2 Mar 2026 (v2)</div>'
    )
    session = _Session([
        _Response(body=api_response),
        _Response(body=empty_api_response),
        _Response(body=fallback_html),
    ])
    monkeypatch.setattr(crawler, "RAW_DIR", tmp_path)
    monkeypatch.setattr(crawler, "DISCOVERY_FILE", tmp_path / "history.jsonl")
    monkeypatch.setattr(
        crawler, "DISCOVERY_MANIFEST", tmp_path / "discovery_status.csv"
    )

    histories = crawler.discover_assigned_versions(
        base_ids, session=session, interval=0
    )

    assert len(histories) == 101
    assert histories[base_ids[0]] == [
        "2603.00000v1", "2603.00000v2", "2603.00000v3"
    ]
    assert histories[base_ids[99]] == ["2603.00099v1"]
    assert histories[base_ids[100]] == ["2603.00100v1", "2603.00100v2"]
    assert len(session.calls) == 3
    assert session.calls[0][0] == "https://export.arxiv.org/api/query"
    assert session.calls[0][1]["params"] == {
        "id_list": ",".join(base_ids[:100]),
        "max_results": 100,
    }
    assert session.calls[1][1]["params"] == {
        "id_list": base_ids[100],
        "max_results": 1,
    }
    assert session.calls[2][0] == f"https://arxiv.org/abs/{base_ids[100]}"
    with (tmp_path / "discovery_status.csv").open(
        encoding="utf-8", newline=""
    ) as file:
        rows = list(csv.DictReader(file))
    assert len(rows) == 101
    assert {row["status"] for row in rows} == {"success"}
    records = [
        json.loads(line)
        for line in (tmp_path / "history.jsonl").read_text().splitlines()
    ]
    assert [record["base_id"] for record in records] == base_ids

    resumed_session = _Session([])
    assert crawler.discover_assigned_versions(
        base_ids, session=resumed_session, interval=0
    ) == histories
    assert resumed_session.calls == []


def test_extract_archive_keeps_only_tex_and_bib(tmp_path):
    archive = io.BytesIO(_archive_bytes({
        "paper/main.tex": r"\documentclass{article}",
        "paper/references.bib": "@article{x}",
        "paper/figure.png": "not source",
        "../outside.tex": "unsafe",
    }))

    tex_count, bib_count, source_bytes_before_filter, source_bytes = extract_tex_bib_archive(
        archive, tmp_path / "version"
    )

    assert (tex_count, bib_count) == (1, 1)
    assert source_bytes_before_filter == (
        len(r"\documentclass{article}") + len("@article{x}")
        + len("not source") + len("unsafe")
    )
    assert source_bytes == len(r"\documentclass{article}") + len("@article{x}")
    assert (tmp_path / "version" / "paper" / "main.tex").exists()
    assert (tmp_path / "version" / "paper" / "references.bib").exists()
    assert not (tmp_path / "version" / "paper" / "figure.png").exists()
    assert not (tmp_path / "outside.tex").exists()


def test_download_sources_records_version_and_filtered_files(tmp_path):
    archive = _archive_bytes({
        "main.tex": r"\documentclass{article}",
        "image.png": "image",
    })
    session = _Session(_Response(body=archive))
    output_dir = tmp_path / "sources"
    manifest_path = tmp_path / "source_manifest.csv"

    download_sources(
        {"2602.00001": ["2602.00001v1"]},
        session=session,
        output_dir=output_dir,
        manifest_path=manifest_path,
        interval=0,
    )

    assert (output_dir / "2602.00001" / "v1" / "main.tex").exists()
    with manifest_path.open(encoding="utf-8", newline="") as file:
        row = next(csv.DictReader(file))
    assert row["status"] == "success"
    assert row["tex_files"] == "1"
    assert row["source_bytes_before_filter"] == str(
        len(r"\documentclass{article}") + len("image")
    )
    assert row["source_bytes"] == str(len(r"\documentclass{article}"))
    assert len(session.calls) == 1


def test_semantic_scholar_saves_all_references_and_manifest(tmp_path):
    payload = {
        "data": [
            {"citedPaper": {
                "paperId": "s2-1",
                "title": "ArXiv reference",
                "authors": [{"name": "Author"}],
                "externalIds": {"ArXiv": "2401.00001"},
            }},
            {"citedPaper": {
                "paperId": "s2-2",
                "title": "Non-arXiv reference",
                "authors": [],
                "externalIds": {},
            }},
        ]
    }
    session = _Session(_Response(payload=payload))
    output_dir = tmp_path / "semantic_scholar"
    manifest_path = tmp_path / "semantic_manifest.csv"

    crawl_semantic_scholar(
        ["2602.00001"],
        session=session,
        output_dir=output_dir,
        manifest_path=manifest_path,
        interval=0,
    )

    result = json.loads((output_dir / "2602.00001.json").read_text(encoding="utf-8"))
    assert len(result["references"]) == 2
    assert session.calls[0][1]["params"]["fields"].startswith("citedPaper.paperId")
    with manifest_path.open(encoding="utf-8", newline="") as file:
        row = next(csv.DictReader(file))
    assert row["status"] == "success"
    assert row["reference_count"] == "2"
    assert row["arxiv_reference_count"] == "1"


def test_semantic_scholar_interval_depends_on_api_key(tmp_path, monkeypatch):
    intervals = []

    class _RecordingPacer:
        def __init__(self, interval):
            intervals.append(interval)

        def wait(self):
            pass

    monkeypatch.setattr(crawler, "RequestPacer", _RecordingPacer)
    monkeypatch.setenv("S2_API_KEY", "test-key")
    keyed_session = _Session(_Response(payload={"data": []}))
    crawler.crawl_semantic_scholar(
        ["2602.00001"],
        session=keyed_session,
        output_dir=tmp_path / "keyed",
        manifest_path=tmp_path / "keyed.csv",
    )
    assert intervals[-1] == 1.1
    assert keyed_session.headers["x-api-key"] == "test-key"

    monkeypatch.delenv("S2_API_KEY")
    crawler.crawl_semantic_scholar(
        ["2602.00002"],
        session=_Session(_Response(payload={"data": []})),
        output_dir=tmp_path / "unkeyed",
        manifest_path=tmp_path / "unkeyed.csv",
    )
    assert intervals[-1] == 3.1


def test_main_runs_semantic_scholar_concurrently_with_arxiv_stages(monkeypatch, capsys):
    s2_started = threading.Event()
    arxiv_started = threading.Event()
    s2_active = threading.Event()
    events = []

    def fake_s2(base_ids):
        events.append("s2-start")
        print("Semantic Scholar: start")
        s2_started.set()
        assert arxiv_started.wait(timeout=2)
        events.append("s2-active")
        print("Semantic Scholar: active")
        s2_active.set()

    def fake_discover(base_ids):
        assert s2_started.wait(timeout=2)
        events.append("arxiv-start")
        print("arXiv: discover")
        arxiv_started.set()
        assert s2_active.wait(timeout=2)
        events.append("arxiv-active")
        print("arXiv: active")
        return {"2603.10580": ["2603.10580v1"]}

    monkeypatch.setattr(crawler, "assigned_base_ids", lambda: ["2603.10580"])
    monkeypatch.setattr(crawler, "_load_histories", lambda ids: {})
    monkeypatch.setattr(crawler, "crawl_semantic_scholar", fake_s2)
    monkeypatch.setattr(crawler, "discover_assigned_versions", fake_discover)
    monkeypatch.setattr(
        crawler, "crawl_arxiv_metadata", lambda histories: events.append("metadata")
    )
    monkeypatch.setattr(
        crawler, "download_sources", lambda histories: events.append("source")
    )

    assert crawler.main([
        "--discover", "--metadata", "--source", "--semantic-scholar", "--limit", "20"
    ]) == 0
    assert events.index("s2-start") < events.index("arxiv-start")
    assert events.index("s2-active") < events.index("arxiv-active")
    assert events.index("metadata") < events.index("source")
    log_lines = capsys.readouterr().out.splitlines()
    assert log_lines.index("Semantic Scholar: start") < log_lines.index(
        "arXiv: discover"
    )
    assert log_lines.index("Semantic Scholar: active") < log_lines.index(
        "arXiv: active"
    )


def test_semantic_scholar_fetches_all_pages():
    responses = [
        _Response(payload={"data": [
            {"citedPaper": {"paperId": "s2-1"}},
            {"citedPaper": {"paperId": "s2-2"}},
        ]}),
        _Response(payload={"data": [
            {"citedPaper": {"paperId": "s2-3"}},
        ]}),
    ]
    session = _Session(responses)

    references = _fetch_all_semantic_scholar_references(
        "2603.10581", session, RequestPacer(0), page_size=2
    )

    assert [reference["paperId"] for reference in references] == [
        "s2-1", "s2-2", "s2-3"
    ]
    assert [call[1]["params"]["offset"] for call in session.calls] == [0, 2]
    assert all(call[1]["params"]["limit"] == 2 for call in session.calls)


def test_build_tables_matches_milestone_one_schema(tmp_path, monkeypatch):
    config = tmp_path / "assignments.csv"
    config.write_text(
        "student_id,member_name,start_month,start_id,end_month,end_id\n"
        "1,Member,2026-03,10580,2026-03,10581\n",
        encoding="utf-8",
    )
    metadata = tmp_path / "metadata.jsonl"
    metadata.write_text(
        json.dumps({
            "id": "2603.10580v1",
            "title": "First version",
            "authors": ["A. Author"],
            "published": "2026-03-10T00:00:00+00:00",
            "updated": "2026-03-10T00:00:00+00:00",
        }) + "\n"
        + json.dumps({
            "id": "2603.10580v2",
            "title": "Revised title",
            "authors": ["A. Author", "B. Author"],
            "published": "2026-03-10T00:00:00+00:00",
            "updated": "2026-04-02T00:00:00+00:00",
            "journal_ref": "Example Journal",
        }) + "\n",
        encoding="utf-8",
    )
    s2_dir = tmp_path / "s2"
    s2_dir.mkdir()
    (s2_dir / "2603.10580.json").write_text(json.dumps({
        "arxiv_id": "2603.10580",
        "references": [
            {
                "paperId": "s2-1",
                "title": "A reference",
                "authors": [{"name": "Ref Author"}],
                "publicationDate": "2020-05-01",
                "externalIds": {"ArXiv": "2001.00001"},
            },
            {"paperId": "s2-2", "externalIds": {}},
        ],
    }), encoding="utf-8")
    papers_path = tmp_path / "papers.parquet"
    references_path = tmp_path / "references.parquet"
    manifest_path = tmp_path / "crawl_manifest.csv"
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    monkeypatch.setattr(table_builder, "RAW_DIR", raw_dir)
    monkeypatch.setattr(table_builder, "SOURCE_DIR", raw_dir / "sources")
    monkeypatch.setattr(table_builder, "SEMANTIC_SCHOLAR_DIR", s2_dir)
    monkeypatch.setattr(table_builder, "DISCOVERY_FILE", raw_dir / "history.jsonl")
    monkeypatch.setattr(table_builder, "DISCOVERY_MANIFEST", raw_dir / "discovery.csv")
    monkeypatch.setattr(table_builder, "METADATA_MANIFEST", raw_dir / "metadata.csv")
    monkeypatch.setattr(table_builder, "SOURCE_MANIFEST", raw_dir / "source.csv")
    monkeypatch.setattr(
        table_builder, "SEMANTIC_SCHOLAR_MANIFEST", raw_dir / "semantic.csv"
    )

    paper_count, reference_count, manifest_count = build_tables(
        config, metadata, s2_dir, papers_path, references_path, manifest_path
    )

    import pyarrow.parquet as pq

    papers = pq.read_table(papers_path)
    references = pq.read_table(references_path)
    assert (paper_count, reference_count, manifest_count) == (1, 1, 3)
    assert papers.schema.names == [
        "arxiv_id", "title", "authors", "submission_date", "revised_dates", "venue"
    ]
    assert references.schema.names == [
        "arxiv_id", "ref_arxiv_id", "ref_title", "ref_authors",
        "ref_submission_date", "ref_s2_id",
    ]
    paper_rows = papers.to_pylist()
    assert paper_rows[0]["title"] == "Revised title"
    assert paper_rows[0]["submission_date"] == "2026-03-10"
    assert paper_rows[0]["revised_dates"] == ["2026-04-02"]
    assert paper_rows[0]["venue"] == "Example Journal"
    assert references.to_pylist()[0]["ref_arxiv_id"] == "2001.00001"
    with manifest_path.open(encoding="utf-8", newline="") as file:
        manifest_rows = list(csv.DictReader(file))
    assert [row["metadata_status"] for row in manifest_rows] == [
        "success", "success", "not_attempted"
    ]
