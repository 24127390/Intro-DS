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
│   ├── processed/       # Cleaned and processed data
│   └── src/             # Currently existing folder; purpose to be clarified
├── docs/                # Technical documentation
├── notebooks/           # Jupyter notebooks for milestones
├── src/
│   ├── crawling/        # arXiv paper crawling
│   ├── latexParser/     # LaTeX parsing
│   ├── matching/        # Reference matching
│   ├── preprocessing/   # Data preprocessing
│   ├── te/              # Purpose to be clarified
│   └── utils/           # Shared utilities
├── tests/               # Automated tests
├── _config.yml
├── _toc.yml
├── .gitignore
├── README.md
└── requirements.txt
## Setup

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
pip install -r requirements.txt
```

## Usage

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

Run all four stages together to discover missing version histories, fetch
metadata, download sources, and crawl references. Semantic Scholar runs in a
background thread while the arXiv stages run. For a small smoke run, add
`--limit 20`:

```bash
python -m src.crawling.source_and_references --discover --metadata --source --semantic-scholar --limit 20
```

By default, arXiv and Semantic Scholar requests are paced at 3.1 seconds per
request. To request a Semantic Scholar API key, use the
[Semantic Scholar API key form](https://www.semanticscholar.org/product/api#api-key-form).
Set the key in the environment before running the crawler; with `S2_API_KEY`
set, Semantic Scholar requests use a 1.1-second interval:

```powershell
$env:S2_API_KEY = "your-api-key"
```

```bash
export S2_API_KEY="your-api-key"
```

Do not commit the key. Semantic Scholar can take many hours for all assigned
IDs. Run only one crawler instance at a time per shared network/IP to avoid
exceeding provider rate limits.

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

## Data and Reproducibility

The shared configuration records the arXiv ID ranges assigned to each group member. Large generated datasets should be stored separately when necessary. Data processing and output formats will be documented as the pipeline is finalized.

## Project Deliverables

- Jupyter Book covering Milestones 1 and 2, with subsequent updates as required.
- GitHub repository containing the pipeline source code and documentation.
- Individual Hugging Face datasets for each member's assigned ID range.
- Final project poster.
