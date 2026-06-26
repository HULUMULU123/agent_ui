from __future__ import annotations

import hashlib

import numpy as np
import pandas as pd

from .config import AGENT_SOURCE_FIELDS, COURT_FILING_DATE_DEFAULT, MIN_ABS_AMOUNT_FOR_LLM, RANDOM_STATE, SAMPLE_PER_CLUSTER
def make_stable_operation_id(df: pd.DataFrame) -> pd.Series:
    purpose = df.get("purpose", pd.Series([""] * len(df))).fillna("").astype(str).str.strip().str.lower()
    amount = pd.to_numeric(df.get("amount", pd.Series([0] * len(df))), errors="coerce").round(2).fillna(0).astype(str)
    date_part = pd.to_datetime(df.get("date", pd.Series([pd.NaT] * len(df))), errors="coerce").astype(str)
    raw_key = purpose + "|" + amount + "|" + date_part
    return raw_key.apply(lambda x: hashlib.sha256(x.encode("utf-8")).hexdigest()[:16])
def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Приводит типовые русские/английские варианты колонок к контракту агента."""
    result = df.copy()
    mapping_candidates = {
        "date": ["date", "дата", "Дата", "Дата операции", "operation_date", "операционная дата"],
        "amount": ["amount", "сумма", "Сумма", "Сумма операции", "operation_amount"],
        "purpose": ["purpose", "назначение", "Назначение", "Назначение платежа", "cleaned_text", "payment_purpose"],
        "cluster": ["cluster", "кластер", "Cluster", "cluster_id"],
        "anomaly_score": ["anomaly_score", "anomaly", "score", "Аномальность"],
        "anomaly_model_score": ["anomaly_model_score", "model_score", "anomaly_model", "Скор модели"],
        "debit_inn": ["debit_inn", "ИНН дебет", "Дебет ИНН", "inn_debit", "payer_inn"],
        "credit_inn": ["credit_inn", "ИНН кредит", "Кредит ИНН", "inn_credit", "receiver_inn"],
        "debit_name": ["debit_name", "Дебет", "Плательщик", "debit", "payer", "payer_name"],
        "credit_name": ["credit_name", "Кредит", "Получатель", "credit", "receiver", "receiver_name"],
        "collect_all_graph_connections.description": [
            "collect_all_graph_connections.description",
            "graph_connections",
            "connections_description",
            "описание связей",
            "связи",
        ],
    }

    lower_to_original = {str(col).strip().lower(): col for col in result.columns}
    rename: dict[Any, str] = {}
    for canonical, candidates in mapping_candidates.items():
        if canonical in result.columns:
            continue
        for candidate in candidates:
            original = lower_to_original.get(str(candidate).strip().lower())
            if original is not None:
                rename[original] = canonical
                break
    if rename:
        result = result.rename(columns=rename)

    if "amount" not in result.columns:
        debit_amount = _first_existing(result, ["debit_amount", "Дебет сумма", "Сумма дебет"])
        credit_amount = _first_existing(result, ["credit_amount", "Кредит сумма", "Сумма кредит"])
        if debit_amount or credit_amount:
            debit = pd.to_numeric(result[debit_amount], errors="coerce") if debit_amount else 0
            credit = pd.to_numeric(result[credit_amount], errors="coerce") if credit_amount else 0
            result["amount"] = pd.Series(debit).fillna(0) - pd.Series(credit).fillna(0)

    return result
def _first_existing(df: pd.DataFrame, names: list[str]) -> str | None:
    lower = {str(col).strip().lower(): col for col in df.columns}
    for name in names:
        if name in df.columns:
            return name
        original = lower.get(name.strip().lower())
        if original is not None:
            return original
    return None
def prepare_transactions(
    df: pd.DataFrame,
    *,
    court_filing_date: str = COURT_FILING_DATE_DEFAULT,
    min_abs_amount_for_llm: float = MIN_ABS_AMOUNT_FOR_LLM,
    sample_per_cluster: int = SAMPLE_PER_CLUSTER,
    random_state: int = RANDOM_STATE,
) -> tuple[pd.DataFrame, pd.DataFrame, list[str]]:
    """Подготовительный слой из тетрадки: типы, interval, idx, LLM-выборка."""
    result = normalize_columns(df)
    warnings: list[str] = []

    for col in ["date", "amount", "purpose", "debit_inn", "credit_inn"]:
        if col not in result.columns:
            warnings.append(f"В файле нет колонки `{col}`; создано безопасное значение по умолчанию.")

    if "date" not in result.columns:
        result["date"] = pd.NaT
    result["date"] = pd.to_datetime(result["date"], errors="coerce")

    if "amount" not in result.columns:
        result["amount"] = 0.0
    result["amount"] = pd.to_numeric(result["amount"], errors="coerce").fillna(0.0)

    if "purpose" not in result.columns:
        result["purpose"] = ""
    result["purpose"] = result["purpose"].fillna("").astype(str)

    if "cluster" not in result.columns:
        warnings.append("Колонка `cluster` отсутствует; создана техническая кластеризация по направлению и назначению.")
        result["cluster"] = _fallback_clusters(result)
    result["cluster"] = pd.to_numeric(result["cluster"], errors="coerce").fillna(0).astype(int)

    if "anomaly_score" not in result.columns:
        result["anomaly_score"] = _amount_rank_score(result["amount"])
    result["anomaly_score"] = pd.to_numeric(result["anomaly_score"], errors="coerce").fillna(0.0)

    if "anomaly_model_score" not in result.columns:
        result["anomaly_model_score"] = result["anomaly_score"]
    result["anomaly_model_score"] = pd.to_numeric(result["anomaly_model_score"], errors="coerce").fillna(0.0)

    for col in ["debit_inn", "credit_inn", "debit_name", "credit_name"]:
        if col not in result.columns:
            result[col] = ""
        result[col] = result[col].fillna("").astype(str)

    if "collect_all_graph_connections.description" not in result.columns:
        result["collect_all_graph_connections.description"] = ""
    result["collect_all_graph_connections.description"] = result["collect_all_graph_connections.description"].fillna("").astype(str)

    result["court_filing_date"] = pd.to_datetime(court_filing_date, errors="coerce")
    result["interval_days"] = (result["date"] - result["court_filing_date"]).dt.days
    result["interval"] = days_to_period(result["interval_days"])
    result["direction"] = np.where(result["amount"] >= 0, "прибытие", "убытие")

    if "idx" not in result.columns and "txn_id" in result.columns:
        result["idx"] = result["txn_id"].astype(str)
    elif "idx" in result.columns:
        result["idx"] = result["idx"].astype(str)
    else:
        result["idx"] = make_stable_operation_id(result)

    agent_df = result[[col for col in AGENT_SOURCE_FIELDS if col in result.columns]].copy()
    sampled = result[result["amount"].abs() >= min_abs_amount_for_llm].copy()
    if sampled.empty:
        sampled = result.copy()
    sampled = diverse_cluster_sample(
        sampled,
        feature_cols=["anomaly_model_score", "interval_days", "anomaly_score"],
        per_cluster=sample_per_cluster,
        random_state=random_state,
    )
    sampled = sampled[sampled["cluster"] != -1].copy()
    if sampled.empty:
        sampled = result.copy()

    return agent_df, sampled, warnings
def days_to_period(series: pd.Series) -> pd.Series:
    s = pd.to_numeric(series, errors="coerce")
    sign = np.where(s >= 0, "+ ", "- ")
    abs_days = s.abs()
    conditions = [
        abs_days < 30,
        (abs_days >= 30) & (abs_days < 90),
        (abs_days >= 90) & (abs_days < 180),
        (abs_days >= 180) & (abs_days < 365),
        (abs_days >= 365) & (abs_days < 1095),
        abs_days >= 1095,
    ]
    choices = ["<1 месяца", "1-3 месяца", "3-6 месяцев", "6-12 месяцев", "1-3 года", "более 3 лет"]
    period = np.select(conditions, choices, default="неизвестно")
    return pd.Series(np.where(s.isna(), "неизвестно", sign + period), index=series.index)
def _amount_rank_score(amount: pd.Series) -> pd.Series:
    abs_amount = pd.to_numeric(amount, errors="coerce").abs().fillna(0)
    if abs_amount.max() <= 0:
        return pd.Series([0.0] * len(abs_amount), index=amount.index)
    return abs_amount.rank(pct=True).round(4)
def _fallback_clusters(df: pd.DataFrame) -> pd.Series:
    purpose = df.get("purpose", pd.Series([""] * len(df))).fillna("").astype(str).str.lower()
    direction = np.where(pd.to_numeric(df.get("amount", 0), errors="coerce").fillna(0) >= 0, "in", "out")
    bucket = purpose.str.extract(r"(налог|займ|аренд|зарплат|постав|подряд|перевод|комисс|кредит|процент)", expand=False).fillna("other")
    raw = pd.Series(direction, index=df.index).astype(str) + ":" + bucket.astype(str)
    codes, _ = pd.factorize(raw)
    return pd.Series(codes, index=df.index)
def diverse_cluster_sample(
    df: pd.DataFrame,
    feature_cols: list[str],
    cluster_col: str = "cluster",
    per_cluster: int = 5,
    total_budget: int | None = None,
    random_state: int = 42,
) -> pd.DataFrame:
    if df.empty:
        return df.copy()
    sampled = df.copy().reset_index(drop=False).rename(columns={"index": "_orig_index"})
    for col in feature_cols:
        if col not in sampled.columns:
            sampled[col] = 0.0
        sampled[col] = pd.to_numeric(sampled[col], errors="coerce").fillna(0.0)
    if cluster_col not in sampled.columns:
        sampled[cluster_col] = 0

    rows = []
    for _, group in sampled.groupby(cluster_col, sort=False):
        k = min(per_cluster, len(group))
        rows.append(farthest_point_sampling(group, feature_cols, k))
    sampled = pd.concat(rows, ignore_index=True) if rows else sampled

    if total_budget is not None and len(sampled) > total_budget:
        sampled = sampled.sample(total_budget, random_state=random_state)
    sampled = sampled.sort_values([cluster_col, "_orig_index"])
    return df.iloc[sampled["_orig_index"].astype(int).to_list()].copy()
def farthest_point_sampling(group: pd.DataFrame, feature_cols: list[str], k: int) -> pd.DataFrame:
    if k >= len(group):
        return group.copy()
    X = group[feature_cols].to_numpy(dtype=float)
    center = X.mean(axis=0, keepdims=True)
    first = int(np.sqrt(((X - center) ** 2).sum(axis=1)).argmax())
    selected = [first]
    remaining = set(range(len(group))) - {first}
    while len(selected) < k and remaining:
        X_sel = X[selected]
        candidates = np.array(sorted(remaining))
        dists = np.sqrt(((X[candidates, None, :] - X_sel[None, :, :]) ** 2).sum(axis=2))
        pick = int(candidates[dists.min(axis=1).argmax()])
        selected.append(pick)
        remaining.remove(pick)
    return group.iloc[selected].copy()
