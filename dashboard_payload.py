from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parent
DEFAULT_OUTPUT_PATH = ROOT / "assets" / "dashboard_payload.json"


def _records(value: Any) -> list[dict[str, Any]]:
    """Accepts list[dict], pandas DataFrame, or None."""
    if value is None:
        return []
    if hasattr(value, "to_dict"):
        try:
            return [dict(row) for row in value.to_dict(orient="records")]
        except TypeError:
            pass
    if isinstance(value, list):
        return [dict(row) for row in value if isinstance(row, Mapping)]
    return []


def _clean_none(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {str(k): _clean_none(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_clean_none(v) for v in obj]
    # pandas/numpy NaN support without importing pandas/numpy
    try:
        if obj != obj:
            return None
    except Exception:
        pass
    return obj


def build_dashboard_payload(
    *,
    transactions: Any = None,
    documents: Any = None,
    charts: dict[str, list[dict[str, Any]]] | None = None,
    signals: Any = None,
    legal_report: Any = None,
    counterparty_registry: Any = None,
    modal: dict[str, dict[str, str]] | None = None,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Single contract for moving results from a Jupyter agent into the Gradio UI.

    Minimal required shape:
    {
      "transactions": [{"date", "time", "doc", "type", "category", "counterparty", "inn", "kpp", "amount"}],
      "charts": {
        "cashFlowMonthly": [{"month", "incoming", "outgoing"}],
        "expenseCategories": [{"category", "value"}],
        "topCounterparties": [{"name", "value"}],
        "riskDistribution": [{"level", "value"}],
        "newCounterparties": [{"week", "newPartners", "checkPartners"}],
        "dailyAmountBuckets": [{"bucket", "value"}],
        "dailyActivity": [{"day", "incoming", "outgoing"}]
      }
    }
    """
    payload = {
        "meta": meta or {
            "appTitle": "Финансовый анализ",
            "appSubtitle": "банковских выписок",
        },
        "documents": _records(documents),
        "transactions": _records(transactions),
        "charts": charts or {},
        "signals": _records(signals),
        "legalReport": _records(legal_report),
        "counterpartyRegistry": _records(counterparty_registry),
        "modal": modal or {},
    }
    return _clean_none(payload)


def save_dashboard_payload(payload: dict[str, Any], path: str | Path = DEFAULT_OUTPUT_PATH) -> Path:
    """Writes the only data file used by app.py."""
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(_clean_none(payload), ensure_ascii=False, indent=2), encoding="utf-8")
    return output_path


def write_dashboard_payload(path: str | Path = DEFAULT_OUTPUT_PATH, **kwargs: Any) -> Path:
    """Convenience wrapper for notebooks: build payload and save it in one call."""
    return save_dashboard_payload(build_dashboard_payload(**kwargs), path=path)
