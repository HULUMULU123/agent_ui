from __future__ import annotations

from collections import Counter
import math
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from .config import REQUIRED_TRANSACTION_COLUMNS
from .fallback import risk_class, risk_label
from .output_normalizer import normalize_agent_output
from .preprocessing import _first_existing, normalize_columns
from .utils import clean_text, ensure_list, format_money, int_safe, short_string
def build_payload_from_agent_outputs(
    input_df: pd.DataFrame,
    prepared_df: pd.DataFrame,
    sampled_df: pd.DataFrame,
    analysis_df: pd.DataFrame,
    filename: str,
    warnings: list[str] | None = None,
    *,
    cluster_strategy: dict[Any, dict[str, Any]] | None = None,
    risk_db: Any | None = None,
    second_pass_updated: int = 0,
) -> dict[str, Any]:
    warnings = warnings or []
    analysis = normalize_agent_output(analysis_df.copy(), sampled_df)
    prepared = prepared_df.copy()

    transactions = []
    for _, row in analysis.iterrows():
        transactions.append({
            "idx": row.get("idx", ""),
            "date": row.get("date", ""),
            "cluster_id": row.get("cluster_id", ""),
            "amount": format_money(row.get("amount", 0)),
            "transaction_category": row.get("transaction_category", ""),
            "counterparty": row.get("counterparty", row.get("counterparty_category", "")),
            "inn": row.get("counterparty_inn", ""),
            "risk_level": row.get("risk_label", risk_label(int_safe(row.get("risk_level", 0)))),
            "connection_basis": row.get("connection_summary", ""),
            "legal_qualification": row.get("legal_qualification", ""),
            "challenge_readiness": row.get("challenge_readiness", ""),
            "recommendation": row.get("recommendation", ""),
            "analysis_source": row.get("analysis_source", "llm"),
            "propagation_confidence": row.get("propagation_confidence", "high"),
            "needs_review": bool(row.get("needs_review", False)),
        })

    statement_summary = build_statement_summary(input_df, prepared, sampled_df, warnings)
    output_summary = build_output_summary(analysis)
    charts = build_charts(prepared, analysis)
    charts["analysisSourceDistribution"] = build_analysis_source_chart(analysis)
    documents = [{
        "document": filename,
        "type": Path(filename).suffix.replace(".", "").upper() or "FILE",
        "uploaded": datetime.now().strftime("%d.%m.%Y, %H:%M"),
        "status": "проанализирован",
        "statusClass": "green",
    }]

    risk_counts = analysis["risk_level"].apply(int_safe).value_counts().to_dict() if "risk_level" in analysis.columns else {}
    high_count = int(risk_counts.get(3, 0))
    medium_count = int(risk_counts.get(2, 0))
    low_count = int(sum(v for k, v in risk_counts.items() if k in (0, 1)))
    risk_sum = output_summary["risk_amount_value"]

    signals = build_signals_from_analysis(analysis, warnings)
    connection_highlights = build_connection_highlights(analysis)
    review_queue = build_review_queue(analysis)
    risk_memory_view = build_risk_memory_view(analysis, risk_db)
    risk_grade_by_inn = {row["inn"]: row["riskGrade"] for row in risk_memory_view if row.get("inn")}
    registry = build_counterparty_registry(analysis, risk_grade_by_inn)
    legal_conclusion = build_legal_conclusion(analysis, warnings, cluster_strategy or {}, second_pass_updated)
    main_tables = build_main_tables(input_df, analysis)
    gray_zone = build_gray_zone(analysis)

    needs_review_count = int(analysis.get("needs_review", pd.Series(dtype=bool)).astype(bool).sum()) if not analysis.empty else 0

    summary = {
        "inputRows": int(len(input_df)),
        "inputColumns": int(len(input_df.columns)),
        "preparedRows": int(len(prepared_df)),
        "sampledRows": int(len(sampled_df)),
        "clusters": int(prepared_df["cluster"].nunique()) if "cluster" in prepared_df.columns else 0,
        "datePeriod": statement_summary["datePeriod"],
        "incomingAmount": statement_summary["incomingAmount"],
        "outgoingAmount": statement_summary["outgoingAmount"],
        "netAmount": statement_summary["netAmount"],
        "uniqueCounterparties": statement_summary["uniqueCounterparties"],
        "missingCoreFields": statement_summary["missingCoreFields"],
        "knownInnCount": statement_summary["knownInnCount"],
        "highRisk": high_count,
        "mediumRisk": medium_count,
        "lowRisk": low_count,
        "riskAmount": format_money(risk_sum),
        "legalRoutes": output_summary["legalRoutes"],
        "strongConnections": output_summary["strongConnections"],
        "documentsRequired": output_summary["documentsRequired"],
        "challengeReady": output_summary["challengeReady"],
        "topAmountSum": output_summary["topAmountSum"],
        "needsReviewCount": needs_review_count,
        "secondPassUpdated": int(second_pass_updated),
        "warnings": warnings,
    }

    return {
        "meta": {
            "appTitle": "Финансовый анализ",
            "appSubtitle": "агент банкротного риска операций",
            "lastUpdated": datetime.now().isoformat(timespec="seconds"),
            "mode": "real_or_fallback_agent",
            "sourceFile": filename,
        },
        "summary": summary,
        "statementSummary": statement_summary,
        "outputSummary": {k: v for k, v in output_summary.items() if not k.endswith("_value")},
        "documents": documents,
        "transactions": transactions,
        "charts": charts,
        "signals": signals,
        "connectionHighlights": connection_highlights,
        "reviewQueue": review_queue,
        "riskMemory": risk_memory_view,
        "counterpartyRegistry": registry,
        "legalConclusion": legal_conclusion,
        "mainTables": main_tables,
        "grayZone": gray_zone,
        "modal": build_modal_texts(analysis),
    }
def build_statement_summary(input_df: pd.DataFrame, prepared: pd.DataFrame, sampled: pd.DataFrame, warnings: list[str]) -> dict[str, Any]:
    df = prepared.copy()
    dates = pd.to_datetime(df.get("date", pd.Series(dtype="datetime64[ns]")), errors="coerce")
    amounts = pd.to_numeric(df.get("amount", pd.Series(dtype=float)), errors="coerce").fillna(0.0)
    incoming = float(amounts[amounts >= 0].sum())
    outgoing = float((-amounts[amounts < 0]).sum())
    inn_values = pd.concat([
        df.get("debit_inn", pd.Series(dtype=str)).fillna("").astype(str),
        df.get("credit_inn", pd.Series(dtype=str)).fillna("").astype(str),
    ], ignore_index=True)
    known_inn = int(inn_values.str.replace(r"\D", "", regex=True).str.len().isin([10, 12]).sum())
    counterparties = pd.concat([
        df.get("debit_name", pd.Series(dtype=str)).fillna("").astype(str),
        df.get("credit_name", pd.Series(dtype=str)).fillna("").astype(str),
    ], ignore_index=True)
    unique_cp = int(counterparties[counterparties.str.strip() != ""].nunique())
    missing_required = [col for col in sorted(REQUIRED_TRANSACTION_COLUMNS) if col not in normalize_columns(input_df).columns]
    period = "не определен"
    if dates.notna().any():
        period = f"{dates.min().strftime('%d.%m.%Y')} — {dates.max().strftime('%d.%m.%Y')}"
    return {
        "inputRows": int(len(input_df)),
        "inputColumns": int(len(input_df.columns)),
        "preparedRows": int(len(df)),
        "sampledRows": int(len(sampled)),
        "datePeriod": period,
        "incomingAmount": format_money(incoming),
        "outgoingAmount": format_money(outgoing),
        "netAmount": format_money(incoming - outgoing),
        "uniqueCounterparties": unique_cp,
        "knownInnCount": known_inn,
        "clusters": int(df["cluster"].nunique()) if "cluster" in df.columns else 0,
        "missingCoreFields": ", ".join(missing_required) if missing_required else "нет",
        "warningsCount": int(len(warnings)),
    }
def build_output_summary(analysis: pd.DataFrame) -> dict[str, Any]:
    if analysis.empty:
        return {
            "analyzedRows": 0,
            "legalRoutes": 0,
            "strongConnections": 0,
            "documentsRequired": 0,
            "challengeReady": 0,
            "riskAmount": "0 ₽",
            "risk_amount_value": 0.0,
            "topAmountSum": "0 ₽",
        }
    routes = analysis.get("legal_route", pd.Series(dtype=str)).fillna("").astype(str)
    strengths = analysis.get("connection_strength", pd.Series(dtype=str)).fillna("").astype(str).str.lower()
    docs_total = int(sum(len(ensure_list(v)) for v in analysis.get("recommended_documents", pd.Series(dtype=object))))
    ready = analysis.get("challenge_readiness", pd.Series(dtype=str)).fillna("").astype(str).str.lower()
    risk_mask = analysis.get("risk_level", pd.Series([0] * len(analysis))).apply(int_safe) >= 2
    amount_abs = pd.to_numeric(analysis.get("amount", pd.Series(dtype=float)), errors="coerce").abs()
    risk_amount_value = float(amount_abs[risk_mask].sum())
    top_amount_sum = float(amount_abs.sort_values(ascending=False).head(4).sum())
    return {
        "analyzedRows": int(len(analysis)),
        "legalRoutes": int(routes[routes.str.strip() != ""].nunique()),
        "strongConnections": int(strengths.str.contains("сильн|высок", regex=True).sum()),
        "documentsRequired": docs_total,
        "challengeReady": int(ready.str.contains("высок|готов|достаточ", regex=True).sum()),
        "riskAmount": format_money(risk_amount_value),
        "risk_amount_value": risk_amount_value,
        "topAmountSum": format_money(top_amount_sum),
    }
def build_signals_from_analysis(analysis: pd.DataFrame, warnings: list[str]) -> list[dict[str, Any]]:
    if analysis.empty:
        return [{"label": "Нет результата агента", "count": 0, "className": "gray", "description": "Анализ еще не запускался или не вернул операций."}]
    strengths = analysis.get("connection_strength", pd.Series(dtype=str)).fillna("").astype(str).str.lower()
    quals = analysis.get("legal_qualification", pd.Series(dtype=str)).fillna("").astype(str).str.lower()
    docs_required = int(sum(len(ensure_list(v)) for v in analysis.get("recommended_documents", pd.Series(dtype=object))))
    challenge = analysis.get("challenge_readiness", pd.Series(dtype=str)).fillna("").astype(str).str.lower()
    high_conn = int(strengths.str.contains("сильн|высок", regex=True).sum())
    medium_conn = int(strengths.str.contains("средн", regex=True).sum())
    route_613 = int(quals.str.contains(r"61\.3|предпочт", regex=True).sum())
    route_612 = int(quals.str.contains(r"61\.2|неравноцен|вред", regex=True).sum())
    ready = int(challenge.str.contains("высок|готов|достаточ", regex=True).sum())
    return [
        {"label": "Сильная связь сторон", "count": high_conn, "className": "yellow" if high_conn else "green", "description": "Контрагент связан с должником (бенефициар, руководитель, аффилированность)."},
        {"label": "Средняя связь сторон", "count": medium_conn, "className": "yellow" if medium_conn else "gray", "description": "Есть косвенные признаки связи (общий адрес, телефон, цепочка контрагентов)."},
        {"label": "Маршрут 61.3", "count": route_613, "className": "yellow" if route_613 else "gray", "description": "Признаки предпочтительного удовлетворения отдельного кредитора."},
        {"label": "Маршрут 61.2", "count": route_612, "className": "yellow" if route_612 else "gray", "description": "Признаки неравноценности встречного предоставления или вреда кредиторам."},
        {"label": "Документы к запросу", "count": docs_required, "className": "yellow" if docs_required else "green", "description": "Всего документов, которые агент рекомендует запросить по операциям."},
        {"label": "Готовность высокая", "count": ready, "className": "red" if ready else "gray", "description": "Операции, по которым уже достаточно оснований для оспаривания."},
        {"label": "Предупреждения входа", "count": int(len(warnings)), "className": "yellow" if warnings else "green", "description": "Проблемы качества входного файла (отсутствующие колонки и т.п.)."},
    ]
def build_connection_highlights(analysis: pd.DataFrame, limit: int = 12) -> list[dict[str, Any]]:
    """Список конкретных операций с заметной связью сторон (не агрегат, а перечень)."""
    if analysis.empty or "connection_strength" not in analysis.columns:
        return []
    tmp = analysis.copy()
    strengths = tmp["connection_strength"].fillna("").astype(str).str.lower()
    mask = strengths.str.contains("сильн|средн", regex=True)
    subset = tmp[mask].copy()
    if subset.empty:
        return []
    subset["risk_numeric"] = subset.get("risk_level", pd.Series([0] * len(subset))).apply(int_safe)
    subset = subset.sort_values("risk_numeric", ascending=False).head(limit)
    rows = []
    for _, row in subset.iterrows():
        rows.append({
            "idx": row.get("idx", ""),
            "counterparty": clean_text(row.get("counterparty", "")),
            "inn": clean_text(row.get("counterparty_inn", "")),
            "summary": clean_text(row.get("connection_summary", "")),
            "strength": clean_text(row.get("connection_strength", "")),
            "risk": row.get("risk_label", ""),
            "riskClass": risk_class(int_safe(row.get("risk_level", 0))),
        })
    return rows
def build_analysis_source_chart(analysis: pd.DataFrame) -> list[dict[str, Any]]:
    """Прозрачность метода получения вывода по операции (порождено пропагацией/вторым проходом)."""
    label_map = {
        "llm": "Прямой анализ LLM",
        "propagated": "Перенесено с представителя",
        "llm_second_pass": "Второй проход LLM",
        "none": "Без анализа",
    }
    if analysis.empty or "analysis_source" not in analysis.columns:
        return [{"source": "нет данных", "value": 0}]
    counts = analysis["analysis_source"].fillna("none").replace("", "none").value_counts()
    if counts.empty:
        return [{"source": "нет данных", "value": 0}]
    return [{"source": label_map.get(str(k), str(k)), "value": int(v)} for k, v in counts.items()]
def build_gray_zone(analysis: pd.DataFrame, limit: int = 300) -> dict[str, Any]:
    """"Серая зона" главной страницы: операции без уверенного типа/кластера, которые не
    попадают в обычный поток анализа риска и нуждаются в ручной проверке отдельно от
    рекомендаций по риску 2-3.

    Две независимые причины (могут пересекаться):
    - шумовой кластер (cluster_id == -1) -- adaptive-кластеризация целиком пропускает такие
      операции (см. clustering_strategy.apply_cluster_strategy), сейчас это единственное
      место в интерфейсе, где они вообще видны;
    - неопознанный тип операции -- классификатор (operation_classifier.py) не нашел ни
      предметного слова, ни названного типа договора в назначении платежа.
    """
    empty = {"totalCount": 0, "noiseClusterCount": 0, "unknownTypeCount": 0, "operations": []}
    if analysis.empty:
        return empty

    cluster_series = pd.to_numeric(analysis.get("cluster_id", pd.Series(dtype=float)), errors="coerce")
    is_noise_cluster = cluster_series == -1
    operation_type = analysis.get("operation_type", pd.Series([""] * len(analysis), index=analysis.index)).fillna("").astype(str)
    is_unknown_type = operation_type.str.startswith("Неопознанный платёж")

    mask = is_noise_cluster | is_unknown_type
    if not mask.any():
        return empty

    subset = analysis[mask].copy()
    subset["_is_noise_cluster"] = is_noise_cluster[mask]
    subset["_is_unknown_type"] = is_unknown_type[mask]
    subset["amount_abs"] = pd.to_numeric(subset.get("amount", 0), errors="coerce").abs().fillna(0.0)
    subset = subset.sort_values("amount_abs", ascending=False).head(limit)

    rows = []
    for _, row in subset.iterrows():
        reasons = []
        if bool(row.get("_is_noise_cluster")):
            reasons.append("шумовой кластер")
        if bool(row.get("_is_unknown_type")):
            reasons.append("тип операции не распознан")
        rows.append({
            "idx": row.get("idx", ""),
            "date": row.get("date", ""),
            "amount": format_money(row.get("amount", 0)),
            "counterparty": clean_text(row.get("counterparty", "")),
            "counterpartyInn": clean_text(row.get("counterparty_inn", "")),
            "operationType": clean_text(row.get("operation_type", "")) or "не определен",
            "clusterId": clean_text(row.get("cluster_id", "")),
            "reasons": reasons,
            "recommendedDocuments": ensure_list(row.get("recommended_documents", [])),
        })

    return {
        "totalCount": int(mask.sum()),
        "noiseClusterCount": int(is_noise_cluster.sum()),
        "unknownTypeCount": int(is_unknown_type.sum()),
        "operations": rows,
    }


def build_review_queue(analysis: pd.DataFrame, limit: int = 15) -> list[dict[str, Any]]:
    """Операции, оставшиеся без уверенного анализа (низкая уверенность/нет представителя)."""
    if analysis.empty or "needs_review" not in analysis.columns:
        return []
    subset = analysis[analysis["needs_review"].astype(bool)].copy()
    if subset.empty:
        return []
    subset["amount_abs"] = pd.to_numeric(subset.get("amount", 0), errors="coerce").abs().fillna(0.0)
    subset = subset.sort_values("amount_abs", ascending=False).head(limit)
    status_labels = {
        "sparse_below_threshold": "разреженный кластер, ниже порога суммы",
        "no_representative": "нет представителя в кластере",
        "noise_cluster": "шумовой кластер",
        "no_analysis": "анализ не выполнялся",
    }
    rows = []
    for _, row in subset.iterrows():
        status = clean_text(row.get("propagation_status", ""))
        rows.append({
            "idx": row.get("idx", ""),
            "counterparty": clean_text(row.get("counterparty", "")),
            "amount": format_money(row.get("amount", 0)),
            "reason": status_labels.get(status, status or "требуется ручная проверка"),
        })
    return rows
def build_risk_memory_view(analysis: pd.DataFrame, risk_db: Any | None, limit: int = 20) -> list[dict[str, Any]]:
    """Историческая память риска контрагентов (RiskMemoryDB), встретившихся в анализе."""
    if risk_db is None or analysis.empty or "counterparty_inn" not in analysis.columns:
        return []
    inns = [i for i in analysis["counterparty_inn"].fillna("").astype(str).unique() if i.strip()]
    rows: list[dict[str, Any]] = []
    for inn in inns:
        try:
            current = risk_db.compute_current_risk(inn)
        except Exception:
            continue
        if int(current.get("events_count", 0) or 0) <= 0:
            continue
        match = analysis[analysis["counterparty_inn"] == inn]
        counterparty_name = clean_text(match.iloc[0].get("counterparty", "")) if not match.empty else ""
        rows.append({
            "counterparty": counterparty_name or "не определено",
            "inn": inn,
            "riskScore": round(float(current.get("risk_score", 0.0)) * 100, 1),
            "riskGrade": current.get("risk_grade", "NO_HISTORY"),
            "eventsCount": int(current.get("events_count", 0)),
            "lastEventDate": current.get("last_event_date") or "",
            "explanation": current.get("explanation", ""),
        })
    rows.sort(key=lambda r: r["riskScore"], reverse=True)
    return rows[:limit]
def _aggregate_list_field(df: pd.DataFrame, column: str, limit: int = 12) -> list[dict[str, Any]]:
    counter: Counter[str] = Counter()
    for value in df.get(column, pd.Series(dtype=object)):
        for item in ensure_list(value):
            text = clean_text(item)
            if text:
                counter[text] += 1
    return [{"label": label, "count": count} for label, count in counter.most_common(limit)]
def build_legal_conclusion(
    analysis: pd.DataFrame,
    warnings: list[str],
    cluster_strategy: dict[Any, dict[str, Any]],
    second_pass_updated: int,
) -> dict[str, Any]:
    """Итоговое юридическое заключение: KPI, нормативная база, судебная практика,
    развернутые карточки операций риска >= 2, статистика обработки пайплайна.
    """
    empty_kpis = {
        "totalAnalyzed": 0, "flaggedCount": 0, "flaggedAmount": "0 ₽",
        "needsReviewCount": 0, "secondPassCount": int(second_pass_updated),
    }
    if analysis.empty:
        return {"kpis": empty_kpis, "normativeBase": [], "courtPractice": [], "operations": [], "processingStats": {}}

    tmp = analysis.copy()
    tmp["risk_numeric"] = tmp.get("risk_level", pd.Series([0] * len(tmp))).apply(int_safe)
    flagged = tmp[tmp["risk_numeric"] >= 2].copy()
    flagged["amount_abs"] = pd.to_numeric(flagged.get("amount", 0), errors="coerce").abs().fillna(0.0)

    kpis = {
        "totalAnalyzed": int(len(tmp)),
        "flaggedCount": int(len(flagged)),
        "flaggedAmount": format_money(float(flagged["amount_abs"].sum())) if not flagged.empty else "0 ₽",
        "needsReviewCount": int(tmp.get("needs_review", pd.Series([False] * len(tmp))).astype(bool).sum()),
        "secondPassCount": int(second_pass_updated),
    }

    normative_base = _aggregate_list_field(flagged, "legal_basis")
    court_practice = _aggregate_list_field(flagged, "court_basis")

    operations = []
    if not flagged.empty:
        for _, row in flagged.sort_values("risk_numeric", ascending=False).iterrows():
            operations.append({
                "idx": row.get("idx", ""),
                "date": row.get("date", ""),
                "amount": format_money(row.get("amount", 0)),
                "counterparty": clean_text(row.get("counterparty", "")),
                "inn": clean_text(row.get("counterparty_inn", "")),
                "riskLevel": int_safe(row.get("risk_level", 0)),
                "riskLabel": row.get("risk_label", ""),
                "riskClass": risk_class(int_safe(row.get("risk_level", 0))),
                "transactionCategory": clean_text(row.get("transaction_category", "")),
                "legalQualification": clean_text(row.get("legal_qualification", "")),
                "legalRoute": clean_text(row.get("legal_route", "")),
                "decisionArgumentation": clean_text(row.get("decision_argumentation", "")),
                "riskExplanation": clean_text(row.get("risk_explanation", "")),
                "overallRiskAssessment": clean_text(row.get("overall_risk_assessment", "")),
                "connectionSummary": clean_text(row.get("connection_summary", "")),
                "connectionStrength": clean_text(row.get("connection_strength", "")),
                "challengeReadiness": clean_text(row.get("challenge_readiness", "")),
                "legalBasis": ensure_list(row.get("legal_basis", [])),
                "courtBasis": ensure_list(row.get("court_basis", [])),
                "recommendation": row.get("recommendation", ""),
                "verificationGoal": clean_text(row.get("recommendation_verification_goal", "")),
                "riskChangeConditions": clean_text(row.get("recommendation_risk_change_conditions", "")),
                "operationType": clean_text(row.get("operation_type", "")),
                "documentsNeeded": ensure_list(row.get("recommended_documents", [])),
            })

    processing_stats = {
        "clustersTotal": len(cluster_strategy or {}),
        "clustersCompact": sum(1 for s in (cluster_strategy or {}).values() if s.get("mode") == "compact"),
        "clustersSparse": sum(1 for s in (cluster_strategy or {}).values() if s.get("mode") == "sparse"),
        "warningsCount": len(warnings),
    }

    return {
        "kpis": kpis,
        "normativeBase": normative_base,
        "courtPractice": court_practice,
        "operations": operations,
        "processingStats": processing_stats,
    }
ORIGINAL_PREVIEW_COLUMNS = [
    "debit_inn", "credit_inn", "date", "debit_name", "credit_name",
    "debit_amount", "credit_amount", "purpose",
]


def build_main_tables(input_df: pd.DataFrame, analysis: pd.DataFrame, rows: int | None = None) -> dict[str, Any]:
    """Превью исходной выписки и итоговой таблицы агента для главной страницы.

    `rows=None` — без обрезки: на главной странице таблицы должны листаться
    целиком (постраничный вывод на клиенте уже это умеет), а не показывать
    только первые N операций.
    """
    original = _table_preview(_restrict_to_original_preview_columns(input_df), rows)
    final_cols = [
        "idx", "date", "amount", "transaction_category", "counterparty", "risk_label",
        "legal_qualification", "analysis_source", "propagation_confidence", "recommendation",
    ]
    if analysis.empty:
        final_df = pd.DataFrame(columns=final_cols)
    else:
        final_df = analysis[[c for c in final_cols if c in analysis.columns]].copy()
        if "amount" in final_df.columns:
            final_df["amount"] = final_df["amount"].apply(format_money)
    final = _table_preview(final_df, rows)
    return {"original": original, "finalAnalysis": final}


def _restrict_to_original_preview_columns(input_df: pd.DataFrame) -> pd.DataFrame:
    """Приводит сырую выписку к канонич. именам и оставляет только те колонки,
    которые сотруднику реально нужно видеть в превью (без служебных полей
    вроде cluster/anomaly_score/graph_connections)."""
    normalized = normalize_columns(input_df)
    rename: dict[str, str] = {}
    debit_amount_col = _first_existing(normalized, ["debit_amount", "Дебет сумма", "Сумма дебет"])
    credit_amount_col = _first_existing(normalized, ["credit_amount", "Кредит сумма", "Сумма кредит"])
    if debit_amount_col and debit_amount_col != "debit_amount":
        rename[debit_amount_col] = "debit_amount"
    if credit_amount_col and credit_amount_col != "credit_amount":
        rename[credit_amount_col] = "credit_amount"
    if rename:
        normalized = normalized.rename(columns=rename)
    available = [c for c in ORIGINAL_PREVIEW_COLUMNS if c in normalized.columns]
    return normalized[available] if available else normalized


def _table_preview(df: pd.DataFrame, rows: int | None = None) -> dict[str, Any]:
    head = df if rows is None else df.head(rows)
    head = head.astype(object).where(head.notna(), "")
    columns = [str(col) for col in head.columns]
    records = [{str(k): ("" if v is None else str(v)) for k, v in row.items()} for row in head.to_dict(orient="records")]
    return {"columns": columns, "rows": records}
def build_modal_texts(analysis: pd.DataFrame) -> dict[str, dict[str, str]]:
    return {
        "high": {"title": "Высокий риск", "text": "Операции требуют проверки документов основания, связи сторон, периода и экономического смысла. Сама связь не является доказательством, но влияет на приоритет проверки."},
        "medium": {"title": "Средний риск", "text": "Есть признаки, требующие уточнения. Отсутствие документов не повышает risk_level автоматически, но должно быть отражено в рекомендациях."},
        "low": {"title": "Низкий риск", "text": "По доступным данным нет достаточной совокупности признаков для повышенного риска."},
        "route_613": {"title": "Маршрут 61.3", "text": "Проверяется возможное предпочтительное удовлетворение требования отдельного кредитора: наличие долга, период, осведомленность, другие кредиторы, связь сторон."},
        "route_612": {"title": "Маршрут 61.2", "text": "Проверяется неравноценность или причинение вреда кредиторам: встречное предоставление, рыночность, экономический смысл, связь сторон."},
    }
def build_charts(prepared: pd.DataFrame, analysis: pd.DataFrame) -> dict[str, list[dict[str, Any]]]:
    df = prepared.copy()
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
    if "amount" in df.columns:
        df["amount"] = pd.to_numeric(df["amount"], errors="coerce").fillna(0.0)
    else:
        df["amount"] = 0.0

    cash = []
    if "date" in df.columns and df["date"].notna().any():
        tmp = df.dropna(subset=["date"]).copy()
        tmp["month"] = tmp["date"].dt.to_period("M").astype(str)
        grouped = tmp.groupby("month").agg(
            incoming=("amount", lambda s: float(s[s >= 0].sum()) / 1_000_000),
            outgoing=("amount", lambda s: float((-s[s < 0]).sum()) / 1_000_000),
        ).reset_index().tail(8)
        cash = [{"month": r["month"], "incoming": round(r["incoming"], 2), "outgoing": round(r["outgoing"], 2)} for _, r in grouped.iterrows()]
    if not cash:
        total = float(df["amount"].abs().sum()) / 1_000_000
        cash = [{"month": "Период", "incoming": round(max(total * .55, 0), 2), "outgoing": round(max(total * .45, 0), 2)}]

    category_amounts = grouped_amounts(analysis, "transaction_category", "category", 6, 22)
    top_counterparties = grouped_amounts(analysis, "counterparty", "name", 6, 18)

    risk_dist = []
    if not analysis.empty and "risk_level" in analysis.columns:
        counts = analysis["risk_level"].apply(int_safe).value_counts().sort_index()
        risk_dist = [{"level": risk_label(int(k)), "value": int(v)} for k, v in counts.items()]

    interval_risk = []
    if not analysis.empty:
        tmp = analysis.copy()
        tmp["risk_numeric"] = tmp.get("risk_level", pd.Series([0] * len(tmp))).apply(int_safe)
        if "interval" in tmp.columns:
            grouped = tmp.groupby("interval").agg(newPartners=("idx", "count"), checkPartners=("risk_numeric", lambda s: int((s >= 2).sum()))).reset_index()
            interval_risk = [{"week": short_string(r["interval"], 14), "newPartners": int(r["newPartners"]), "checkPartners": int(r["checkPartners"])} for _, r in grouped.head(8).iterrows()]

    legal_dist = count_distribution(analysis, "legal_route", "route", "value", 6, 24)
    connection_dist = count_distribution(analysis, "connection_strength", "strength", "value", 6, 18)
    readiness_dist = count_distribution(analysis, "challenge_readiness", "readiness", "value", 6, 22)

    return {
        "cashFlowMonthly": cash,
        "expenseCategories": category_amounts or [{"category": "Нет данных", "value": 0}],
        "topCounterparties": top_counterparties or [{"name": "Нет данных", "value": 0}],
        "riskDistribution": risk_dist or [{"level": "Нет данных", "value": 1}],
        "newCounterparties": interval_risk or [{"week": "Нет данных", "newPartners": 0, "checkPartners": 0}],
        "dailyAmountBuckets": build_amount_buckets(df),
        "dailyActivity": build_daily_activity(df),
        "legalQualificationDistribution": legal_dist or [{"route": "Нет данных", "value": 0}],
        "connectionStrengthDistribution": connection_dist or [{"strength": "Нет данных", "value": 0}],
        "challengeReadinessDistribution": readiness_dist or [{"readiness": "Нет данных", "value": 0}],
    }
def grouped_amounts(df: pd.DataFrame, key: str, label_key: str, limit: int, label_limit: int) -> list[dict[str, Any]]:
    if df.empty or key not in df.columns:
        return []
    tmp = df.copy()
    tmp["amount_abs"] = pd.to_numeric(tmp.get("amount", 0), errors="coerce").abs().fillna(0.0)
    grouped = tmp.groupby(key)["amount_abs"].sum().sort_values(ascending=False).head(limit)
    return [{label_key: short_string(k or "Не определено", label_limit), "value": round(float(v) / 1_000_000, 2)} for k, v in grouped.items()]
def count_distribution(df: pd.DataFrame, key: str, label_key: str, value_key: str, limit: int, label_limit: int) -> list[dict[str, Any]]:
    if df.empty or key not in df.columns:
        return []
    counts = df[key].fillna("не установлено").astype(str).replace("", "не установлено").value_counts().head(limit)
    return [{label_key: short_string(k, label_limit), value_key: int(v)} for k, v in counts.items()]
def build_amount_buckets(df: pd.DataFrame) -> list[dict[str, Any]]:
    amounts = pd.to_numeric(df.get("amount", pd.Series(dtype=float)), errors="coerce").abs().fillna(0.0)
    if amounts.empty:
        return [{"bucket": "0", "value": 0}]
    bins = [0, 10_000, 50_000, 100_000, 500_000, 1_000_000, math.inf]
    labels = ["0–10K", "10–50K", "50–100K", "100–500K", "0.5–1M", "1M+"]
    bucketed = pd.cut(amounts, bins=bins, labels=labels, include_lowest=True)
    counts = bucketed.value_counts().reindex(labels, fill_value=0)
    return [{"bucket": str(k), "value": int(v)} for k, v in counts.items()]
def build_daily_activity(df: pd.DataFrame) -> list[dict[str, Any]]:
    if "date" not in df.columns or not pd.to_datetime(df["date"], errors="coerce").notna().any():
        return [{"day": "Нет даты", "incoming": int((df["amount"] >= 0).sum()), "outgoing": int((df["amount"] < 0).sum())}]
    tmp = df.copy()
    tmp["date"] = pd.to_datetime(tmp["date"], errors="coerce")
    tmp = tmp.dropna(subset=["date"])
    tmp["day"] = tmp["date"].dt.strftime("%d.%m")
    grouped = tmp.groupby("day").agg(incoming=("amount", lambda s: int((s >= 0).sum())), outgoing=("amount", lambda s: int((s < 0).sum()))).reset_index().tail(10)
    return [{"day": r["day"], "incoming": int(r["incoming"]), "outgoing": int(r["outgoing"])} for _, r in grouped.iterrows()]
def build_counterparty_registry(analysis: pd.DataFrame, risk_grade_by_inn: dict[str, str] | None = None) -> list[dict[str, Any]]:
    if analysis.empty:
        return []
    risk_grade_by_inn = risk_grade_by_inn or {}
    tmp = analysis.copy()
    tmp["risk_numeric"] = tmp.get("risk_level", pd.Series([0] * len(tmp))).apply(int_safe)
    tmp["amount_abs"] = pd.to_numeric(tmp.get("amount", 0), errors="coerce").abs().fillna(0.0)
    key = "counterparty" if "counterparty" in tmp.columns else "counterparty_category"
    grouped = tmp.groupby(key).agg(
        inn=("counterparty_inn", "first") if "counterparty_inn" in tmp.columns else ("idx", "count"),
        segment=("counterparty_category", "first") if "counterparty_category" in tmp.columns else ("idx", "count"),
        operations=("idx", "count"),
        max_risk=("risk_numeric", "max"),
        amount=("amount_abs", "sum"),
        connection=("connection_strength", "max") if "connection_strength" in tmp.columns else ("risk_numeric", "max"),
    ).sort_values(["max_risk", "amount"], ascending=False).head(20).reset_index()
    rows = []
    for _, r in grouped.iterrows():
        inn = r["inn"] if isinstance(r.get("inn"), str) else ""
        rows.append({
            "counterparty": r[key],
            "inn": inn,
            "segment": r["segment"] if isinstance(r.get("segment"), str) else "не определено",
            "operations": int(r["operations"]),
            "risk": risk_label(int_safe(r["max_risk"])),
            "riskGrade": risk_grade_by_inn.get(inn, "—"),
        })
    return rows
