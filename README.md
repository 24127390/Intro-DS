# Introduction to Data Science — Group 13

## Project Overview

This project is developed for the Introduction to Data Science course. It focuses on collecting and processing arXiv research papers, extracting structured information from LaTeX sources, and performing reference matching according to the official project statement.

## Team Members

| Student ID | Name                 |
|------------|----------------      |
| 24127390   | Nguyen Huu Ngoc Hung |
| 24127456   | Luong Hoang Nam      |
| 24127101   | Thach Cao Phong      |
| 24127559   | Nguyen Thanh Toan    |
| 24127590   | Doan The Viet        |

## Project Structure

Intro-DS/
├── config/              # Shared configuration
├── data/
│   ├── raw/             # Original collected data
│   ├── interim/         # Intermediate data
│   └── processed/       # Cleaned and processed data
├── docs/                # Technical documentation
├── notebooks/           # Jupyter notebooks for milestones
├── src/
│   ├── crawling/        # arXiv paper crawling
│   ├── latexParser/     # LaTeX parsing
│   ├── matching/        # Reference matching
│   ├── preprocessing/   # Data preprocessing
│   ├── utils/           # Shared utilities
│   └── requirements.txt # Dependencies to reproduce the environment
├── tests/               # Automated tests
├── _config.yml
├── _toc.yml
├── .gitignore
├── README.md
└── requirements.txt
## Setup
Python **3.11** is required (same version as the CI workflow).
Create and activate a Python virtual environment, then install dependencies:

```bash
python -m venv .venv
```

On Windows:

```bash
.venv\Scripts\activate
```

On macOS/Linux:

```bash
source .venv/bin/activate
```

Install dependencies:

```bash
pip install -r src/requirements.txt
```

## Usage

### Milestone 1 — Data crawling
The source/reference crawler reads the assigned ID ranges from
`config/aXirPaperID`. It resumes from existing checkpoints and writes raw outputs
and per-paper/per-version manifests under `data/raw`:

```bash
# Discover versions for assigned papers (reuses available history files)
python -m src.crawling.source_and_references --discover

# Fetch metadata for all discovered versions into one JSONL corpus
python -m src.crawling.source_and_references --metadata

# Download every version's source, retaining only .tex and .bib files
python -m src.crawling.source_and_references --source

# Query Semantic Scholar once per assigned paper
python -m src.crawling.source_and_references --semantic-scholar

# Build the Milestone 1 Parquet tables after crawling
python -m src.crawling.build_tables
```

Run `--metadata --source --semantic-scholar` together to discover any missing
version histories and execute the remaining steps. For a small smoke run, add
`--limit 2` to any command. arXiv and Semantic Scholar requests are paced at
3.1 seconds per request, and Semantic Scholar can take many hours for all
assigned IDs. Run only one crawler instance at a time per shared network/IP to
avoid exceeding provider rate limits. An optional Semantic Scholar API key can
be provided through the `S2_API_KEY` environment variable; do not commit the
key.

Source files are stored under `data/raw/sources/<arxiv_id>/v<version>/`.
`source_download_manifest.csv` distinguishes compressed archive size from the
uncompressed source size before and after filtering. Full Semantic Scholar
responses, including references without an arXiv ID, are stored in
`data/raw/semantic_scholar/<arxiv_id>.json`; the corresponding manifest records
request status and reference counts. Non-successful requests remain visible in
the manifests and can be retried by running the same command again. arXiv
metadata for all assigned versions is consolidated in
`arxiv_metadata_all_assignments.jsonl`; pre-existing per-range metadata is
reused as a checkpoint and copied into the consolidated file without duplicate
IDs. Metadata query status is recorded in `arxiv_metadata_manifest.csv`.
Semantic Scholar references use the paginated references endpoint so papers
with more than one page are not silently truncated. The builder writes
`data/processed/papers.parquet` and `data/processed/references.parquet` using
the Milestone 1 schemas; the references table includes only records with an
arXiv identifier, while all returned records remain in the raw JSON files. It
also consolidates the per-stage logs into `data/processed/crawl_manifest.csv`
for notebook coverage analysis.

### Milestone 2 — Hierarchical parsing and standardization

The parser reads the `.tex`/`.bib` sources in `data/raw/sources/<arxiv_id>/v<version>/`.
For each version it finds the root file (the one with `\documentclass`), follows
`\input`/`\include` transitively, builds the hierarchy, normalizes the content and
deduplicates elements across versions.

Main modules in `src/latexParser/`:

- `file_resolver.py` — `LatexFileResolver`: root detection and multi-file gathering.
- `structure_extractor.py` — `PublicationHierarchyBuilder`: hierarchy, sentence splitting,
  cleanup, math normalization and element deduplication.

```bash
# TODO: add the command of the parser entry point once it is finished, e.g.
# python -m src.latexParser.run
```

Planned outputs in `data/processed/`: `elements.parquet`, `hierarchy.parquet`,
`bibtex.parquet` and `parse_manifest.csv` (status `parsed` / `empty` / `failed` per version).

### Milestone 3 — Reference matching

TODO: this section will be completed with the matching pipeline (code in `src/matching/`):

- **Labels:** format and location of the ground truth (manual and automatic pools).
- **Features:** list of engineered features with a short justification for each.
- **Model and evaluation:** chosen supervised formulation and MRR@5 on the test set.
- **Run:** commands to train and evaluate, and to build the `predictions` table.
- **Hugging Face:** how the Parquet tables are exported and published.

### Benchmark

Wall time, peak/average RAM and output size of any stage can be measured with
`src/utils/benchmark.py`. Results are appended to `data/processed/benchmarks.csv`:

```bash
python -m src.utils.benchmark --label m2_parser \
    --output-dir data/processed --n-items <number_of_papers> -- <command>
```

## Data and Reproducibility

The shared configuration records the arXiv ID ranges assigned to each group member. Large generated datasets should be stored separately when necessary. Data processing and output formats will be documented as the pipeline is finalized.

## Project Deliverables

- Jupyter Book covering Milestones 1 and 2, with subsequent updates as required.
- GitHub repository containing the pipeline source code and documentation.
- Individual Hugging Face datasets for each member's assigned ID range.
- Final project poster.
