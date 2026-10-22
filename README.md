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

- `config/`: Shared configuration and assigned arXiv ID ranges.
- `data/`: Raw, intermediate, and processed data.
- `src/crawling/`: Paper and metadata collection.
- `src/latex_parser/`: LaTeX parsing and document structure extraction.
- `src/preprocessing/`: Data cleaning and normalization.
- `src/matching/`: Reference matching and evaluation.
- `tests/`: Automated tests.
- `notebooks/`: Exploratory analysis.
- `docs/`: Technical documentation.

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

Execution instructions for each pipeline module will be added as the modules are implemented.

## Data and Reproducibility

The shared configuration records the arXiv ID ranges assigned to each group member. Large generated datasets should be stored separately when necessary. Data processing and output formats will be documented as the pipeline is finalized.

## Project Deliverables

- Jupyter Book covering Milestones 1 and 2, with subsequent updates as required.
- GitHub repository containing the pipeline source code and documentation.
- Individual Hugging Face datasets for each member's assigned ID range.
- Final project poster.
