from __future__ import annotations

from pathlib import Path

import pandas as pd
def read_statement_table(file_path: str | Path) -> pd.DataFrame:
    """Читает Excel/CSV-файл банковской выписки."""
    path = Path(file_path)
    suffix = path.suffix.lower()

    if suffix in {".xlsx", ".xls"}:
        return pd.read_excel(path)
    if suffix == ".csv":
        return pd.read_csv(path, sep=None, engine="python")

    raise ValueError(f"Неподдерживаемый формат файла: {suffix}. Нужен .xlsx, .xls или .csv")
