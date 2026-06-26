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
    summary: dict[str, Any] | None = None,
    statement_summary: dict[str, Any] | None = None,
    output_summary: dict[str, Any] | None = None,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Единый контракт передачи данных из Jupyter в Gradio UI.

    Минимальные ключи таблицы transactions:
    idx, date, cluster_id, amount, transaction_category, counterparty,
    inn, risk_level, connection_basis, legal_qualification,
    challenge_readiness, recommendation.

    Сигналы должны формироваться не из anomaly_score напрямую, а из результата агента:
    connections_basis / connection_basis, legal_qualification, challenge_criteria,
    recommendation.documents_to_request.
    """
    payload = {
        "meta": meta or {
            "appTitle": "Финансовый анализ",
            "appSubtitle": "агент банкротного риска операций",
        },
        "summary": summary or {},
        "statementSummary": statement_summary or {},
        "outputSummary": output_summary or {},
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
