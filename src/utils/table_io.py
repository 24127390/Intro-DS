"""Shared loader for the pipeline's output tables."""
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
PROCESSED_DIR = ROOT / "data" / "processed"


def load_table(path) -> pd.DataFrame:
    """Load a parquet table, or return an empty DataFrame if it does not exist yet."""
    path = Path(path)
    if not path.exists():
        print(f"Not found yet: {path}")
        return pd.DataFrame()
    return pd.read_parquet(path)