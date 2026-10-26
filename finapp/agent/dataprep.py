"""Загрузка и подготовка выписки: нормализация, проверка, обогащение по ИНН, обезличивание, период."""
from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from .common import (AFFILIATION_COLUMNS, PROFILE_FIELDS, AgentConfig, DataProfile, is_missing, normalize_inn,
                     parse_dates)

logger = logging.getLogger("finapp.agent.dataprep")

INN_COLUMNS = ("debit_inn", "credit_inn")
REQUIRED_TRANSACTION_COLUMNS = {"date", "amount", "purpose", "debit_inn", "credit_inn"}
VALIDATION_COLUMN_GROUPS: list[list[str]] = [
    ["date"], ["debit_inn", "debit_name"], ["credit_inn", "credit_name"], ["amount"], ["purpose"],
]
ISO_FEATURE_LABELS = {
    "log_abs_amount": "крупная сумма", "is_outflow": "списание средств", "round_level": "круглая сумма",
    "amount_vs_cp": "сумма нетипична для этого контрагента",
    "amount_vs_cluster": "сумма нетипична для такого типа платежа",
    "share_of_cp_day": "большая доля в дневном обороте с контрагентом", "cp_freq_rank": "редкий контрагент",
    "days_since_last_cr": "давно не было операций с контрагентом", "credit_inn_spike_7_to_30": "всплеск платежей контрагенту",
    "credit_inn_to_p95_90": "платежи выше недавних максимумов по контрагенту",
    "credit_inn_roll_cnt_7d": "много операций с контрагентом за неделю",
    "debit_inn_spike_7_to_30": "всплеск списаний со счёта должника",
}

# Конвенция знака суммы: сумма по дебету -> плюс -> деньги ПРИБЫВАЮТ на счёт должника; по кредиту -> минус -> УХОДЯТ.
# Парсер выписок отдаёт именно такой знак (списание со счёта владельца — минус).
_POSITIVE, _NEGATIVE = "прибытие", "убытие"
_OUTFLOW_LABELS = {"убытие", "списание", "расход", "кредит", "outflow", "credit", "out"}
_INFLOW_LABELS = {"прибытие", "поступление", "приход", "дебет", "inflow", "debit", "in"}


@dataclass
class PreparedData:
    df: pd.DataFrame
    rejected: pd.DataFrame
    profile: DataProfile
    notes: list[str] = field(default_factory=list)


def humanize_iso_drivers(value: Any) -> str:
    if is_missing(value):
        return ""
    names = [part.strip() for part in str(value).split(",") if part.strip()]
    return "; ".join(ISO_FEATURE_LABELS[name] for name in names if name in ISO_FEATURE_LABELS)


def split_combined_party_column(df: pd.DataFrame, combined_col: str, inn_col: str, name_col: str, notes: list[str]) -> None:
    """Колонка вида '<счёт> <ИНН> <Наименование...>' -> отсутствующие *_inn / *_name."""
    if combined_col not in df.columns or (inn_col in df.columns and name_col in df.columns):
        return
    parts = df[combined_col].fillna("").astype(str).str.strip().str.split(r"\s+", n=2, regex=True)
    if inn_col not in df.columns:
        df[inn_col] = parts.str[1].fillna("").str.strip()
    if name_col not in df.columns:
        df[name_col] = parts.str[2].fillna("").str.strip()
    notes.append(f"Колонка «{combined_col}» разобрана на ИНН и наименование.")


def make_stable_operation_id(df: pd.DataFrame) -> pd.Series:
    """Хеш (назначение, сумма, дата) + порядковый номер среди полных дублей."""
    purpose = df["purpose"].fillna("").astype(str).str.strip().str.lower()
    amount = pd.to_numeric(df["amount"], errors="coerce").round(2).fillna(0).astype(str)
    date_part = df["date"].dt.strftime("%Y-%m-%d").fillna("NaT")
    raw_key = purpose + "|" + amount + "|" + date_part
    raw_key = raw_key + "|" + raw_key.groupby(raw_key).cumcount().astype(str)
    return raw_key.map(lambda key: hashlib.sha256(key.encode("utf-8")).hexdigest()[:16])


def derive_direction(df: pd.DataFrame, notes: list[str]) -> pd.Series:
    """Направление относительно счёта должника по ЗНАКУ суммы; при нуле/пропуске — колонка direction, затем debit/credit_amount."""
    result = pd.Series("не определено", index=df.index, dtype=object)
    amount = pd.to_numeric(df["amount"], errors="coerce") if "amount" in df.columns else pd.Series(np.nan, index=df.index)
    result[amount > 0] = _POSITIVE
    result[amount < 0] = _NEGATIVE

    source = next((df[c] for c in ("direction", "direction_for_llm") if c in df.columns), None)
    if source is not None:
        text = source.astype("string").fillna("").str.strip().str.lower()
        declared = pd.Series("не определено", index=df.index, dtype=object)
        declared[text.isin(_OUTFLOW_LABELS)] = "убытие"
        declared[text.isin(_INFLOW_LABELS)] = "прибытие"
        conflict = result.ne("не определено") & declared.ne("не определено") & result.ne(declared)
        if conflict.any():
            notes.append(f"Знак суммы и колонка направления расходятся в {int(conflict.sum())} операциях: "
                         "принят знак суммы (плюс — поступление, минус — списание).")
        fill = result.eq("не определено")
        result[fill] = declared[fill]

    undefined = result.eq("не определено")
    if undefined.any() and ("debit_amount" in df.columns or "credit_amount" in df.columns):
        debit = pd.to_numeric(df.get("debit_amount"), errors="coerce").fillna(0).abs() if "debit_amount" in df.columns else 0
        credit = pd.to_numeric(df.get("credit_amount"), errors="coerce").fillna(0).abs() if "credit_amount" in df.columns else 0
        result[undefined & (debit > 0)] = "прибытие"
        result[undefined & (credit > 0)] = "убытие"
    if {"atypicality_score", "top_drivers"} & set(df.columns):
        notes.append("Во входе есть колонки iso.ipynb: там amount > 0 означает СПИСАНИЕ, здесь amount > 0 — поступление. "
                     "Проверьте согласованность знака.")
    return result


def _plain_objects(df: pd.DataFrame) -> pd.DataFrame:
    """Nullable-типы pandas (string/Float64 с pd.NA, выдаваемые парсером) -> object/float c None/NaN.
    Иначе `value or ""` и подобные выражения падают на pd.NA."""
    out = df.copy()
    for col in out.columns:
        dtype = out[col].dtype
        if pd.api.types.is_extension_array_dtype(dtype) and not isinstance(dtype, pd.DatetimeTZDtype):
            if pd.api.types.is_numeric_dtype(dtype):
                out[col] = pd.to_numeric(out[col], errors="coerce").astype("float64")
            elif not pd.api.types.is_datetime64_any_dtype(dtype):
                out[col] = out[col].astype(object).where(out[col].notna(), None)
    return out


def load_transactions(raw: pd.DataFrame, cfg: AgentConfig | None = None, *, disable_relations: bool = False,
                      disable_clustering: bool = False) -> tuple[pd.DataFrame, DataProfile, list[str]]:
    notes: list[str] = []
    df = _plain_objects(raw).reset_index(drop=True)

    split_combined_party_column(df, "debit", "debit_inn", "debit_name", notes)
    split_combined_party_column(df, "credit", "credit_inn", "credit_name", notes)
    for col in ("debit_name", "credit_name"):
        if col not in df.columns:
            df[col] = None

    if disable_clustering and "cluster" in df.columns:
        df = df.drop(columns=["cluster"])
    if disable_relations:
        df = df.drop(columns=[c for c in AFFILIATION_COLUMNS if c in df.columns])

    if "amount" in df.columns:
        df["amount"] = pd.to_numeric(df["amount"], errors="coerce")
    elif "debit_amount" in df.columns or "credit_amount" in df.columns:
        debit = pd.to_numeric(df["debit_amount"], errors="coerce").fillna(0) if "debit_amount" in df.columns else 0.0
        credit = pd.to_numeric(df["credit_amount"], errors="coerce").fillna(0) if "credit_amount" in df.columns else 0.0
        df["amount"] = debit - credit
    else:
        raise ValueError("В таблице операций нужна колонка amount (или пара debit_amount / credit_amount).")

    for col in INN_COLUMNS:
        if col not in df.columns:
            df[col] = ""
    missing = sorted(REQUIRED_TRANSACTION_COLUMNS - set(df.columns))
    if missing:
        raise ValueError(f"В таблице операций нет обязательных колонок: {missing}")

    for col in INN_COLUMNS:
        df[col] = df[col].map(normalize_inn)
    df["date"] = parse_dates(df["date"])
    df["direction"] = derive_direction(df, notes)

    if "atypicality_score" in df.columns and "anomaly_score" not in df.columns:
        df["anomaly_score"] = pd.to_numeric(df["atypicality_score"], errors="coerce")
    if "top_drivers" in df.columns:
        df["atypicality_reasons"] = df["top_drivers"].map(humanize_iso_drivers)

    profile = DataProfile()
    profile.clustering_available = "cluster" in df.columns
    if profile.clustering_available:
        df["cluster"] = df["cluster"].fillna(-1)
    profile.has_anomaly_scores = any(col in df.columns for col in ("anomaly_score", "anomaly_model_score"))
    for col in ("anomaly_score", "anomaly_model_score"):
        if col not in df.columns:
            df[col] = np.nan
    profile.has_affiliation = any(col in df.columns for col in AFFILIATION_COLUMNS)
    profile.has_business_profile = any(
        f"{side}_{field_}" in df.columns for side in ("debit", "credit") for field_ in PROFILE_FIELDS)

    if "idx" in df.columns and df["idx"].notna().all() and df["idx"].astype(str).str.strip().ne("").all():
        df["idx"] = df["idx"].astype(str)
    elif "txn_id" in df.columns:
        df["idx"] = df["txn_id"].astype(str)
    else:
        df["idx"] = make_stable_operation_id(df)
    n_dup = int(df["idx"].duplicated().sum())
    if n_dup:
        raise ValueError(f"Дубликаты идентификаторов операций (idx): {n_dup}. Проверьте txn_id во входном файле.")
    return df, profile, notes


def is_blank_series(series: pd.Series) -> pd.Series:
    as_str = series.astype(str).str.strip()
    return series.isna() | as_str.eq("") | as_str.str.lower().isin({"nan", "nat", "none", "<na>"})


def validate_operations(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Группа колонок считается пустой, если пусты все её колонки. Непригодные операции выгружаются отдельно."""
    reasons = pd.Series([[] for _ in range(len(df))], index=df.index)
    for group in VALIDATION_COLUMN_GROUPS:
        present = [col for col in group if col in df.columns]
        if not present:
            continue
        blank = pd.Series(True, index=df.index)
        for col in present:
            blank &= is_blank_series(df[col])
        label = " / ".join(group)
        reasons[blank] = reasons[blank].apply(lambda acc, lab=label: acc + [lab])

    rejected_mask = reasons.apply(bool)
    rejected = df[rejected_mask].copy()
    if not rejected.empty:
        rejected["rejection_reason"] = reasons[rejected_mask].apply("; ".join)
    valid = df[~rejected_mask].copy().reset_index(drop=True)
    return valid, rejected


def enrich_by_inn(df: pd.DataFrame, spark_df: pd.DataFrame | None) -> pd.DataFrame:
    """Признаки контрагента по ИНН для обеих сторон; справочник дедуплицируется, чтобы merge не размножал строки."""
    if spark_df is None or spark_df.empty or "inn" not in spark_df.columns:
        return df.copy()
    spark = spark_df.copy()
    spark["inn"] = spark["inn"].map(normalize_inn)
    result = df.copy()
    reference = spark.drop_duplicates(subset="inn", keep="first")
    for side in ("debit", "credit"):
        key = f"{side}_inn"
        ref = reference.add_prefix(f"{side}_")
        ref = ref[[c for c in ref.columns if c == key or c not in result.columns]]
        result = result.merge(ref, on=key, how="left", validate="many_to_one")
    if len(result) != len(df):
        raise RuntimeError("Обогащение изменило число строк.")
    return result


# ───────────────────────── Обезличивание ─────────────────────────

_SURNAME_ENDINGS = [
    "ов", "ев", "ин", "ын", "ский", "ая", "ой", "ий", "ич", "вич", "ова", "ева", "ина", "ына", "иха",
    "ава", "ица", "иная", "яя", "ькая", "цкая", "цкий", "ской", "цкой", "ни", "нова", "ук", "чук",
]
_SURNAME = r"\b[А-ЯЁ][а-яё]*(?:" + "|".join(_SURNAME_ENDINGS) + r")\b"
_NAME = r"[А-ЯЁ][а-яё]+"
_INITIAL = r"[А-ЯЁ]\."
_FIO_PATTERNS = [re.compile(p) for p in (
    rf"{_SURNAME}\s+{_NAME}\s+{_NAME}",
    rf"{_SURNAME}\s+[А-ЯЁ]\s*[А-ЯЁ]",
    rf"{_SURNAME}\s+{_INITIAL}\s*{_INITIAL}",
    rf"{_INITIAL}\s*{_INITIAL}\s*{_SURNAME}",
    rf"[А-ЯЁ]\s*[А-ЯЁ]\s+{_SURNAME}",
    rf"{_SURNAME}\s+{_NAME}",
    rf"{_NAME}\s+{_SURNAME}",
)]


def clean_fio(text: Any) -> str:
    if is_missing(text):
        return ""
    text = str(text)
    for pattern in _FIO_PATTERNS:
        text = pattern.sub("<ФИО>", text)
    return text


def anonymize_transactions(df: pd.DataFrame) -> pd.DataFrame:
    """ФИО, номера счетов и ИНН в назначении заменяются метками. Наименования сторон остаются для отображения
    сотруднику; в LLM контрагент уходит псевдонимом ORG_N."""
    result = df.copy()
    result["purpose"] = (
        result["purpose"].fillna("").astype(str)
        .str.replace(r"\d{20,22}", "<Номер счёта>", regex=True)
        .str.replace(r"\b\d{10}(?:\d{2})?\b", "<ИНН>", regex=True)
        .map(clean_fio)
    )
    return result


# ───────────────────────── Очищенное назначение ─────────────────────────

try:  # лемматизация необязательна
    import pymorphy3
    _purpose_morph = pymorphy3.MorphAnalyzer()
except Exception:  # noqa: BLE001
    _purpose_morph = None

_MONTH_RE = re.compile(
    r"(?:январ[ьяю]?|феврал[ьяю]?|март[а]?|апрел[ьяю]?|ма[йяю]|июн[ьяю]?|июл[ьяю]?|август[аеу]?|"
    r"сентябр[ьяю]?|октябр[ьяю]?|ноябр[ьяю]?|декабр[ьяю]?|янв|фев|мар|апр|авг|сен|окт|ноя|дек)\b\.?",
    flags=re.IGNORECASE,
)
_ABBREVIATIONS = {
    r"\bзп\b": "заработная плата", r"\bр/с\b": "расчетный счет", r"\bл/с\b": "лицевой счет",
    r"\bсогл\b": "согласно", r"\bдог\b": "договор", r"\bрко\b": "расчетно-кассовое обслуживание",
    r"\bсч\b": "счет", r"\bдс\b": "денежные средства",
}
_PURPOSE_STOPWORDS = {"в", "на", "нее"}


def _lemmatize(word: str) -> str:
    if _purpose_morph is None:
        return word
    parsed = _purpose_morph.parse(word)
    return parsed[0].normal_form if parsed else word


def clean_purpose_text(text: Any) -> str:
    if is_missing(text):
        return ""
    result = _MONTH_RE.sub(" ", str(text))
    result = re.sub(r"UUID\s+[a-f0-9-]+", " ", result, flags=re.IGNORECASE)
    result = re.sub(r"\b\d{1,2}[./-]\d{1,2}[./-]\d{2,4}\b", " ", result)
    result = re.sub(r"(?:в\s+том\s+числе|включая|вкл\.?)\s+ндс[^а-яё]*", " ндс ", result, flags=re.IGNORECASE)
    result = re.sub(r"ндс\s+не\s+облагается|без\s+ндс|не\s+обл\.?\s+ндс", " без ндс ", result, flags=re.IGNORECASE)
    result = re.sub(r"\d+([.,-]\d+)*%?", " ", result)
    for pattern, replacement in _ABBREVIATIONS.items():
        result = re.sub(pattern, replacement, result, flags=re.IGNORECASE)
    result = re.sub(r"[^\w\s]|_", " ", result).lower()
    words = [_lemmatize(w) for w in result.split() if w not in _PURPOSE_STOPWORDS]
    return " ".join(w for w in words if w)


# ───────────────────────── Период относительно даты заявления ─────────────────────────

def days_to_period(days: pd.Series) -> pd.Series:
    """Границы совпадают с периодами подозрительности гл. III.1 Закона о банкротстве (1 мес., 6 мес., 1 год, 3 года)."""
    s = pd.to_numeric(days, errors="coerce")
    before = -s
    period = np.select(
        [s.isna(), s >= 0, before <= 30, before <= 182, before <= 365, before <= 1095],
        ["не определено", "после подачи заявления", "менее 1 месяца до заявления", "1–6 месяцев до заявления",
         "6–12 месяцев до заявления", "1–3 года до заявления"],
        default="более 3 лет до заявления",
    )
    return pd.Series(period, index=days.index)


def prepare_statement(raw: pd.DataFrame, cfg: AgentConfig, spark_df: pd.DataFrame | None = None, *,
                      disable_relations: bool = False, disable_clustering: bool = False) -> PreparedData:
    """Сырая таблица операций -> готовый к анализу DataFrame (idx, direction, purpose_cleaned, interval_days, period)."""
    df, profile, notes = load_transactions(raw, cfg, disable_relations=disable_relations,
                                           disable_clustering=disable_clustering)
    df, rejected = validate_operations(df)
    if df.empty:
        raise ValueError("После проверки не осталось операций, пригодных для анализа "
                         "(нужны дата, стороны, сумма и назначение платежа).")
    if not rejected.empty:
        notes.append(f"Исключено операций с незаполненными обязательными полями: {len(rejected)}.")
    df = enrich_by_inn(df, spark_df)
    profile.has_business_profile = profile.has_business_profile or any(
        f"{side}_{f}" in df.columns for side in ("debit", "credit") for f in PROFILE_FIELDS)
    df = anonymize_transactions(df)
    df["purpose_cleaned"] = df["purpose"].map(clean_purpose_text)
    if not profile.clustering_available:
        df = df.sort_values(["purpose_cleaned", "debit_inn", "credit_inn", "amount"], kind="mergesort",
                            na_position="last").reset_index(drop=True)
        df["cluster"] = np.arange(len(df)) // max(1, int(cfg.max_operations_per_llm_batch))
    filing = pd.Timestamp(cfg.court_filing_date)
    df["interval_days"] = (df["date"] - filing).dt.days
    df["period"] = days_to_period(df["interval_days"])
    return PreparedData(df=df, rejected=rejected, profile=profile, notes=notes)


def rule_skip_mask(df: pd.DataFrame, cfg: AgentConfig) -> pd.Series:
    """Операции старше порога вне периодов подозрительности (ст. 61.2–61.3): в LLM не уходят, risk_level 0 по правилу."""
    years = cfg.rule_skip_older_than_years
    if not years:
        return pd.Series(False, index=df.index)
    return df["interval_days"] < -365 * years
