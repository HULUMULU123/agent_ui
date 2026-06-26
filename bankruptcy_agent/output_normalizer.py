from __future__ import annotations

from typing import Any

import pandas as pd

from .extraction import (
    connection_summary,
    documents_from_output,
    extract_challenge_criteria,
    extract_connection_basis,
    legal_route,
    normalize_connection_strength,
    readiness_from_risk,
    recommendation_summary,
)
from .fallback import classify_transaction_category, deterministic_operation_analysis, infer_counterparty_category, risk_label
from .utils import clean_text, format_date, int_safe, parse_maybe_json, to_float
def normalize_agent_output(analysis: pd.DataFrame, sampled: pd.DataFrame) -> pd.DataFrame:
    """Приводит результат агента к единому формату dashboard.

    Поддерживает:
    - connections_basis из тетрадки;
    - connection_basis, если колонку назвали в единственном числе;
    - legal_qulification, если допущена опечатка;
    - recommendation как строку или dict.
    """
    if analysis is None or analysis.empty:
        return deterministic_operation_analysis(sampled)

    result = analysis.copy()
    rename_map = {}
    if "connection_basis" in result.columns and "connections_basis" not in result.columns:
        rename_map["connection_basis"] = "connections_basis"
    if "legal_qulification" in result.columns and "legal_qualification" not in result.columns:
        rename_map["legal_qulification"] = "legal_qualification"
    if "cluster" in result.columns and "cluster_id" not in result.columns:
        rename_map["cluster"] = "cluster_id"
    if "counterparty_inn" in result.columns and "inn" not in result.columns:
        rename_map["counterparty_inn"] = "inn"
    result = result.rename(columns=rename_map)

    # Обогащаем недостающие поля из sampled по idx.
    sampled_by_idx = sampled.copy()
    sampled_by_idx["idx"] = sampled_by_idx.get("idx", pd.Series([""] * len(sampled_by_idx))).astype(str)
    source_lookup = sampled_by_idx.set_index("idx", drop=False).to_dict(orient="index") if "idx" in sampled_by_idx.columns else {}

    normalized_rows = []
    for _, row in result.iterrows():
        raw = row.to_dict()
        idx = str(raw.get("idx", ""))
        src = source_lookup.get(idx, {})
        merged = {**src, **raw}
        normalized_rows.append(normalize_single_analysis_row(merged))
    return pd.DataFrame(normalized_rows)
def normalize_single_analysis_row(row: dict[str, Any]) -> dict[str, Any]:
    risk_level_value = int_safe(row.get("risk_level", 0))
    connection = extract_connection_basis(row)
    challenge = extract_challenge_criteria(row)
    recommendation_obj = parse_maybe_json(row.get("recommendation", {}))
    legal_qualification = clean_text(row.get("legal_qualification") or row.get("legal_qulification") or "не установлена")
    amount = to_float(row.get("amount"), 0.0)
    counterparty_name = clean_text(row.get("counterparty") or row.get("credit_name") or row.get("debit_name") or row.get("counterparty_category") or "Контрагент не определен")
    inn = clean_text(row.get("counterparty_inn") or row.get("inn") or row.get("credit_inn") or row.get("debit_inn") or "")
    category = clean_text(row.get("transaction_category") or classify_transaction_category(clean_text(row.get("purpose")), amount))
    rec_summary = recommendation_summary(recommendation_obj, row, category, risk_level_value)
    docs = documents_from_output(recommendation_obj, challenge, category, risk_level_value)
    challenge.setdefault("documents_needed", docs)

    return {
        "cluster_id": row.get("cluster_id", row.get("cluster", "")),
        "idx": clean_text(row.get("idx")),
        "date": format_date(row.get("date")),
        "interval": clean_text(row.get("interval")),
        "transaction_category": category,
        "amount": amount,
        "counterparty": counterparty_name,
        "counterparty_inn": inn,
        "counterparty_category": clean_text(row.get("counterparty_category") or infer_counterparty_category(counterparty_name, inn)),
        "connections_basis": connection,
        "connection_summary": connection_summary(connection),
        "connection_strength": normalize_connection_strength(connection.get("connection_strength")),
        "challenge_criteria": challenge,
        "challenge_readiness": clean_text(challenge.get("challenge_readiness") or readiness_from_risk(risk_level_value)),
        "risk_level": risk_level_value,
        "risk_label": risk_label(risk_level_value),
        "legal_qualification": legal_qualification,
        "legal_route": legal_route(legal_qualification),
        "legal_basis": parse_maybe_json(row.get("legal_basis", [])),
        "court_basis": parse_maybe_json(row.get("court_basis", [])),
        "decision_argumentation": clean_text(row.get("decision_argumentation")),
        "risk_explanation": clean_text(row.get("risk_explanation")),
        "recommendation": rec_summary,
        "recommended_documents": docs,
        "used_tools": parse_maybe_json(row.get("used_tools", [])),
        "status": clean_text(row.get("status", "success")),
        "error": clean_text(row.get("error", "")),
    }
