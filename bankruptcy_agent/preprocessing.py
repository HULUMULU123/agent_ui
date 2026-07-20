from __future__ import annotations

import hashlib
import logging
import re
from typing import Any

import numpy as np
import pandas as pd

from .clustering_strategy import apply_cluster_strategy
from .config import COURT_FILING_DATE_DEFAULT, MIN_ABS_AMOUNT_FOR_LLM
from .similarity import fit_similarity_space

logger = logging.getLogger("bankruptcy_agent.preprocessing")

# Операция допускается к анализу, только если заполнены дата, сумма, назначение
# платежа и обе стороны операции. Сторона считается известной, если заполнена
# хотя бы одна колонка пары (например, есть debit_inn ЛИБО debit_name).
# Перенесено из agent_v3_adaptive_clusters_1.ipynb, "Проверка операций перед анализом".
VALIDATION_COLUMN_GROUPS: list[list[str]] = [
    ["date"],
    ["debit_inn", "debit_name"],
    ["credit_inn", "credit_name"],
    ["amount"],
    ["purpose"],
]


def is_blank_series(series: pd.Series) -> pd.Series:
    """Возвращает булеву маску пустых значений колонки (NaN/NaT, пустая строка,
    строка из одних пробелов)."""
    as_str = series.astype(str).str.strip()
    return (
        series.isna()
        | as_str.eq("")
        | as_str.str.lower().isin({"nan", "nat", "none", "<na>"})
    )


def validate_operations(
    df: pd.DataFrame,
    groups: list[list[str]] = VALIDATION_COLUMN_GROUPS,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Делит операции на пригодные для анализа и отбракованные.

    Возвращает (valid_df, rejected_df). В rejected_df добавляется колонка
    `rejection_reason` со списком незаполненных групп колонок.
    """
    result = df.copy()
    reasons = pd.Series([[] for _ in range(len(result))], index=result.index)

    for group in groups:
        present = [col for col in group if col in result.columns]
        if not present:
            logger.warning("Проверка операций: ни одна из колонок %s не найдена, группа пропущена.", group)
            continue

        group_blank = pd.Series(True, index=result.index)
        for col in present:
            group_blank &= is_blank_series(result[col])

        if group_blank.any():
            label = " / ".join(group)
            reasons[group_blank] = reasons[group_blank].apply(lambda acc, l=label: acc + [l])
            logger.info("Проверка операций: пустая группа %s у %d операций.", label, int(group_blank.sum()))

    rejected_mask = reasons.apply(bool)
    rejected_df = result[rejected_mask].copy()
    if not rejected_df.empty:
        rejected_df["rejection_reason"] = reasons[rejected_mask].apply("; ".join)

    valid_df = result[~rejected_mask].copy().reset_index(drop=True)
    logger.info(
        "Проверка операций завершена: пригодных=%d, исключено=%d из %d.",
        len(valid_df), len(rejected_df), len(result),
    )
    return valid_df, rejected_df


def make_stable_operation_id(df: pd.DataFrame) -> pd.Series:
    purpose = df.get("purpose", pd.Series([""] * len(df))).fillna("").astype(str).str.strip().str.lower()
    amount = pd.to_numeric(df.get("amount", pd.Series([0] * len(df))), errors="coerce").round(2).fillna(0).astype(str)
    date_part = pd.to_datetime(df.get("date", pd.Series([pd.NaT] * len(df))), errors="coerce").astype(str)
    raw_key = purpose + "|" + amount + "|" + date_part
    # Порядковый номер внутри группы одинаковых ключей защищает от коллизий
    # между полностью идентичными операциями (тот же платеж дважды в день).
    dedup_suffix = raw_key.groupby(raw_key).cumcount().astype(str)
    raw_key = raw_key + "|" + dedup_suffix
    return raw_key.apply(lambda x: hashlib.sha256(x.encode("utf-8")).hexdigest()[:16])


def clean_fio(text: Any) -> str:
    """Маскирует ФИО в тексте (перенесено из тетрадки, ячейка 07)."""
    if pd.isna(text):
        return ""
    text = str(text)

    surname_endings = [
        "ов", "ев", "ин", "ын", "ский", "ая", "ой", "ий", "ич", "вич",
        "ова", "ева", "ина", "ына", "иха", "ава", "ица", "иная",
        "яя", "ькая", "цкая", "цкий", "ской", "цкой", "ни", "нова",
        "ук", "чук",
    ]
    surname_pattern = r"\b[А-ЯЁ][а-яё]*(?:" + "|".join(surname_endings) + r")\b"
    name_pattern = r"[А-ЯЁ][а-яё]+"
    initial_pattern = r"[А-ЯЁ]\."

    patterns = [
        rf"{surname_pattern}\s+{name_pattern}\s+{name_pattern}",
        rf"{surname_pattern}\s+[А-ЯЁ]\s*[А-ЯЁ]",
        rf"{surname_pattern}\s+{initial_pattern}\s*{initial_pattern}",
        rf"{initial_pattern}\s*{initial_pattern}\s*{surname_pattern}",
        rf"[А-ЯЁ]\s*[А-ЯЁ]\s+{surname_pattern}",
        rf"{surname_pattern}\s+{name_pattern}",
        rf"{name_pattern}\s+{surname_pattern}",
    ]
    for pattern in patterns:
        text = re.sub(pattern, "<ФИО>", text)
    return text


def anonymize_transactions(df: pd.DataFrame) -> pd.DataFrame:
    """Обезличивает назначение платежа и ФИО сторон перед передачей в LLM."""
    result = df.copy()
    if "purpose" in result.columns:
        result["purpose"] = (
            result["purpose"]
            .fillna("")
            .astype(str)
            .str.replace(r"\d{20,22}", "<Номер Счета>", regex=True)
            .str.replace(r"\d{10,12}", "<ИНН>", regex=True)
            .apply(clean_fio)
        )
    for col in ("debit_name", "credit_name"):
        if col in result.columns:
            result[col] = result[col].apply(clean_fio)
    return result


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


def enrich_by_inn(df: pd.DataFrame, enrichment_df: pd.DataFrame) -> pd.DataFrame:
    """Добавляет признаки контрагентов отдельно для дебетовой и кредитовой стороны
    (перенесено из тетрадки, enrich_by_inn). `enrichment_df` — таблица со
    столбцом `inn` и признаками контрагента (регистрация, статус, ОКВЭД,
    судебные дела, налоговая задолженность и т.д.), см. SPARK_ENRICHMENT_PATH.

    Если таблица обогащения пуста или не содержит `inn`, выписка возвращается
    без изменений — анализ продолжается без обогащения, а не падает.
    """
    if enrichment_df is None or enrichment_df.empty or "inn" not in enrichment_df.columns:
        return df.copy()

    result = df.copy()
    reference = enrichment_df.copy()
    reference["inn"] = reference["inn"].astype(str).str.strip()

    for side in ("debit", "credit"):
        inn_col = f"{side}_inn"
        if inn_col not in result.columns:
            continue
        result[inn_col] = result[inn_col].astype(str).str.strip()
        prefixed = reference.add_prefix(f"{side}_")
        result = result.merge(prefixed, on=inn_col, how="left")

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
    enrichment_df: pd.DataFrame | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[Any, dict[str, Any]], pd.DataFrame, list[str], pd.DataFrame]:
    """Подготовительный слой из тетрадки: типы, interval, idx, адаптивная LLM-выборка.

    Возвращает (prepared, df_unique, cluster_strategy, diagnostics_df, warnings,
    rejected_df). `prepared` — вся выписка с техническими полями (используется для
    распространения анализа на 100% операций). `df_unique` — представители
    компактных кластеров + разреженные кластеры целиком (адаптивная стратегия
    плотности, см. clustering_strategy.py), это то, что реально уходит в LLM.
    `rejected_df` — операции, отбракованные validate_operations (не заполнены
    обязательные поля), в анализ не попадают.
    """
    result = normalize_columns(df)
    warnings: list[str] = []

    result, rejected_df = validate_operations(result)
    if not rejected_df.empty:
        warnings.append(
            f"Исключено из анализа {len(rejected_df)} операций: не заполнены обязательные поля "
            "(дата, сумма, назначение платежа или обе стороны операции)."
        )
    if result.empty:
        raise ValueError(
            "После проверки не осталось ни одной операции, пригодной для анализа. "
            "Проверьте заполненность колонок: дата, сумма, назначение платежа, "
            "стороны операции (ИНН/наименование)."
        )

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

    if enrichment_df is not None and not enrichment_df.empty:
        result = enrich_by_inn(result, enrichment_df)

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

    result["is_below_llm_threshold"] = result["amount"].abs() < min_abs_amount_for_llm

    # Обезличивание применяется ко всей выписке до расчета сходства и до
    # отправки в LLM, чтобы текстовая метрика сравнивала операции в одном и
    # том же (уже обезличенном) представлении на обеих сторонах распространения.
    result = anonymize_transactions(result)

    # Единое метрическое пространство для плотности/выбора представителей/
    # распространения фиксируется по ВСЕЙ подготовленной выписке.
    fit_similarity_space(result)

    df_sample = result[~result["is_below_llm_threshold"]].copy().reset_index(drop=True)
    if df_sample.empty:
        df_sample = result.copy().reset_index(drop=True)

    df_unique, cluster_strategy, diagnostics_df = apply_cluster_strategy(df_sample)
    if df_unique.empty:
        df_unique = df_sample.copy()
        if "batch_group" not in df_unique.columns:
            df_unique["batch_group"] = df_unique["cluster"].astype(str)
        if "selection_mode" not in df_unique.columns:
            df_unique["selection_mode"] = "full_sparse"

    return result, df_unique, cluster_strategy, diagnostics_df, warnings, rejected_df


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
