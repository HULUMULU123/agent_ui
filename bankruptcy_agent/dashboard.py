from __future__ import annotations

from collections import Counter
import math
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from .config import REQUIRED_TRANSACTION_COLUMNS
from .extraction import detail_key_for_route
from .fallback import risk_class, risk_label
from .output_normalizer import normalize_agent_output
from .preprocessing import normalize_columns
from .utils import clean_text, ensure_list, format_money, int_safe, short_string, unique_strings
def build_payload_from_agent_outputs(
    input_df: pd.DataFrame,
    prepared_df: pd.DataFrame,
    sampled_df: pd.DataFrame,
    analysis_df: pd.DataFrame,
    filename: str,
    warnings: list[str] | None = None,
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
        })

    statement_summary = build_statement_summary(input_df, prepared, sampled_df, warnings)
    output_summary = build_output_summary(analysis)
    charts = build_charts(prepared, analysis)
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
    legal_report = build_legal_report(analysis)
    registry = build_counterparty_registry(analysis)

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
        "legalReport": legal_report,
        "counterpartyRegistry": registry,
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
        }
    routes = analysis.get("legal_route", pd.Series(dtype=str)).fillna("").astype(str)
    strengths = analysis.get("connection_strength", pd.Series(dtype=str)).fillna("").astype(str).str.lower()
    docs_total = int(sum(len(ensure_list(v)) for v in analysis.get("recommended_documents", pd.Series(dtype=object))))
    ready = analysis.get("challenge_readiness", pd.Series(dtype=str)).fillna("").astype(str).str.lower()
    risk_mask = analysis.get("risk_level", pd.Series([0] * len(analysis))).apply(int_safe) >= 2
    risk_amount_value = float(pd.to_numeric(analysis.get("amount", pd.Series(dtype=float)), errors="coerce").abs()[risk_mask].sum())
    return {
        "analyzedRows": int(len(analysis)),
        "legalRoutes": int(routes[routes.str.strip() != ""].nunique()),
        "strongConnections": int(strengths.str.contains("сильн|высок", regex=True).sum()),
        "documentsRequired": docs_total,
        "challengeReady": int(ready.str.contains("высок|готов|достаточ", regex=True).sum()),
        "riskAmount": format_money(risk_amount_value),
        "risk_amount_value": risk_amount_value,
    }
def build_signals_from_analysis(analysis: pd.DataFrame, warnings: list[str]) -> list[dict[str, Any]]:
    if analysis.empty:
        return [{"label": "Нет результата агента", "count": 0, "className": "gray"}]
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
        {"label": "Сильная связь сторон", "count": high_conn, "className": "yellow" if high_conn else "green"},
        {"label": "Средняя связь сторон", "count": medium_conn, "className": "yellow" if medium_conn else "gray"},
        {"label": "Маршрут 61.3", "count": route_613, "className": "yellow" if route_613 else "gray"},
        {"label": "Маршрут 61.2", "count": route_612, "className": "yellow" if route_612 else "gray"},
        {"label": "Документы к запросу", "count": docs_required, "className": "yellow" if docs_required else "green"},
        {"label": "Готовность высокая", "count": ready, "className": "red" if ready else "gray"},
        {"label": "Предупреждения входа", "count": int(len(warnings)), "className": "yellow" if warnings else "green"},
    ]
def build_legal_report(analysis: pd.DataFrame) -> list[dict[str, Any]]:
    rows = []
    if analysis.empty:
        return rows
    tmp = analysis.copy()
    tmp["risk_numeric"] = tmp.get("risk_level", pd.Series([0] * len(tmp))).apply(int_safe)
    tmp["amount_abs"] = pd.to_numeric(tmp.get("amount", 0), errors="coerce").abs().fillna(0.0)
    for route, group in tmp.groupby(tmp.get("legal_route", pd.Series(["не установлено"] * len(tmp))).fillna("не установлено")):
        rows.append({
            "sum": format_money(group["amount_abs"].sum()),
            "operations": int(len(group)),
            "risk": short_string(route, 42),
            "riskClass": risk_class(int(group["risk_numeric"].max()) if not group.empty else 0),
            "detail": detail_key_for_route(str(route)),
        })
    return sorted(rows, key=lambda x: x["operations"], reverse=True)
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
def build_counterparty_registry(analysis: pd.DataFrame) -> list[dict[str, Any]]:
    if analysis.empty:
        return []
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
        rows.append({
            "counterparty": r[key],
            "inn": r["inn"] if isinstance(r.get("inn"), str) else "",
            "segment": r["segment"] if isinstance(r.get("segment"), str) else "не определено",
            "operations": int(r["operations"]),
            "risk": risk_label(int_safe(r["max_risk"])),
        })
    return rows
