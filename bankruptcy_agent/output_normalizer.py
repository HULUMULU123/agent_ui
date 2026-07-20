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
    # operation_type -- единственное поле типа операции, которое возвращает анализатор
    # (см. ANALYZER_PROMPT, ШАГ 1a: анализатор обязан вернуть его без изменений).
    # transaction_category сохранён как fallback для старых записей/deterministic-режима.
    category = clean_text(row.get("operation_type") or row.get("transaction_category") or classify_transaction_category(clean_text(row.get("purpose")), amount))
    rec_summary = recommendation_summary(recommendation_obj, row, category, risk_level_value)
    docs = documents_from_output(
        recommendation_obj, challenge, category, risk_level_value,
        classifier_documents=row.get("requested_documents"),
    )
    challenge.setdefault("documents_needed", docs)
    verification_goal = clean_text(recommendation_obj.get("verification_goal")) if isinstance(recommendation_obj, dict) else ""
    risk_change_conditions = clean_text(recommendation_obj.get("risk_change_conditions")) if isinstance(recommendation_obj, dict) else ""

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
        "overall_risk_assessment": clean_text(row.get("overall_risk_assessment")),
        "recommendation": rec_summary,
        "recommendation_verification_goal": verification_goal,
        "recommendation_risk_change_conditions": risk_change_conditions,
        "recommended_documents": docs,
        # Тип операции от классификатора (operation_classifier.py): "classified" --
        # определён LLM/fallback-ом напрямую по этой операции (была в выборке
        # кластера), "propagated" -- унаследован от похожей по purpose операции
        # своего кластера. Анализатору менять operation_type запрещено (см. дальше
        # "серую зону" и рекомендации).
        "operation_type": clean_text(row.get("operation_type") or ""),
        "operation_type_source": clean_text(row.get("operation_type_source") or ""),
        "operation_type_similarity": to_float(row.get("operation_type_similarity"), 1.0),
        "operation_type_need_review": bool(row.get("operation_type_need_review", False)),
        "used_tools": parse_maybe_json(row.get("used_tools", [])),
        "status": clean_text(row.get("status", "success")),
        "error": clean_text(row.get("error", "")),
        # Технические поля распространения/второго прохода: безопасные
        # дефолты для fallback-режима без propagation (все операции "llm").
        "analysis_source": clean_text(row.get("analysis_source") or "llm"),
        "matched_representative_idx": clean_text(row.get("matched_representative_idx") or ""),
        "similarity_score": to_float(row.get("similarity_score"), 1.0),
        "propagation_confidence": clean_text(row.get("propagation_confidence") or "high"),
        "propagation_status": clean_text(row.get("propagation_status") or "analyzed_by_llm"),
        "propagation_note": clean_text(row.get("propagation_note") or ""),
        "needs_review": bool(row.get("needs_review", False)),
    }
