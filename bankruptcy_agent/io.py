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


def load_counterparty_enrichment(path: str | Path | None) -> pd.DataFrame:
    """Читает таблицу обогащения контрагентов по ИНН (перенесено из тетрадки,
    load_spark_enrichment). Путь берется из SPARK_ENRICHMENT_PATH.

    Если путь не задан или файл не найден — возвращает пустой DataFrame, и
    обогащение молча пропускается (так же ведет себя тетрадка).
    """
    if not path:
        return pd.DataFrame()
    resolved = Path(path)
    if not resolved.exists():
        return pd.DataFrame()

    suffix = resolved.suffix.lower()
    if suffix in {".xlsx", ".xls"}:
        table = pd.read_excel(resolved)
    elif suffix == ".csv":
        table = pd.read_csv(resolved, sep=None, engine="python")
    elif suffix == ".parquet":
        table = pd.read_parquet(resolved)
    else:
        return pd.DataFrame()

    if "inn" in table.columns:
        table["inn"] = table["inn"].astype(str).str.strip()
    return table
