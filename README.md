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

Execution instructions for each pipeline module will be added as the modules are implemented.

## Data and Reproducibility

The shared configuration records the arXiv ID ranges assigned to each group member. Large generated datasets should be stored separately when necessary. Data processing and output formats will be documented as the pipeline is finalized.

## Project Deliverables

- Jupyter Book covering Milestones 1 and 2, with subsequent updates as required.
- GitHub repository containing the pipeline source code and documentation.
- Individual Hugging Face datasets for each member's assigned ID range.
- Final project poster.
