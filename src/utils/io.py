from pathlib import Path

import pandas as pd

from src.settings import PROJECT_ROOT


def save_parquet(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)


def load_parquet(path: Path) -> pd.DataFrame:
    return pd.read_parquet(path)


def resolve_path(relative: Path | str) -> Path:
    return PROJECT_ROOT / relative
