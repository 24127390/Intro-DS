"""Configuration reader for arXiv crawling ID assignments."""

import csv
from pathlib import Path
from typing import Any, Dict, List

CONFIG_PATH = Path("config/aXirPaperID")


def _format_arxiv_id(month_str: str, num_id: int) -> str:
    """Format year-month and numeric ID into canonical arXiv ID format YYMM.NNNNN.

    Args:
        month_str: Month string in 'YYYY-MM' format (e.g. '2025-12').
        num_id: Numeric paper ID (e.g. 3231).

    Returns:
        Formatted arXiv ID string (e.g. '2512.03231').
    """
    cleaned_month = month_str.replace("-", "")
    yymm = cleaned_month[2:] if len(cleaned_month) == 6 else cleaned_month
    return f"{yymm}.{str(num_id).zfill(5)}"


def load_member_range(
    student_id: str, config_path: Path = CONFIG_PATH
) -> Dict[str, Any]:
    """Load a single member's assigned arXiv ID range from config.

    Args:
        student_id: The student ID string (e.g. '24127559').
        config_path: Path to the CSV config file. Defaults to config/aXirPaperID.

    Returns:
        Dictionary containing member details and full list of assigned arXiv IDs.

    Raises:
        FileNotFoundError: If config file is not found.
        ValueError: If student_id is not in config.
    """
    if not config_path.exists():
        raise FileNotFoundError(f"Configuration file not found: {config_path}")

    with config_path.open("r", encoding="utf-8") as file:
        reader = csv.DictReader(file)
        for row in reader:
            if row.get("student_id") == student_id:
                start_id = int(row["start_id"])
                end_id = int(row["end_id"])
                month = row["start_month"]

                arxiv_ids: List[str] = [
                    _format_arxiv_id(month, i) for i in range(start_id, end_id + 1)
                ]

                return {
                    "student_id": row["student_id"],
                    "member_name": row["member_name"],
                    "start_month": row["start_month"],
                    "start_id": start_id,
                    "end_month": row["end_month"],
                    "end_id": end_id,
                    "arxiv_ids": arxiv_ids,
                }

    raise ValueError(f"Student ID '{student_id}' not found in configuration.")


def load_all_members(config_path: Path = CONFIG_PATH) -> List[Dict[str, Any]]:
    """Load all members and their assigned arXiv ID ranges.

    Args:
        config_path: Path to the CSV config file.

    Returns:
        List of member dictionaries.
    """
    if not config_path.exists():
        raise FileNotFoundError(f"Configuration file not found: {config_path}")

    members: List[Dict[str, Any]] = []
    with config_path.open("r", encoding="utf-8") as file:
        reader = csv.DictReader(file)
        for row in reader:
            members.append(load_member_range(row["student_id"], config_path))
    return members
