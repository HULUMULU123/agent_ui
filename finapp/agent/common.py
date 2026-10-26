"""Общие утилиты агента: конфиг, нормализация ИНН и дат, разбор JSON-ответов LLM, очистка текстов."""
from __future__ import annotations

import json
import logging
import math
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, List, Optional

import numpy as np
import pandas as pd

from .prompts import HYPOTHESIS_LABELS, RISK_FACTORS, ROUTES

logger = logging.getLogger("finapp.agent")

MODEL_NAME = "GigaChat-3-Ultra"

AFFILIATION_COLUMNS = ("relations_json", "collect_all_graph_connections")
PROFILE_FIELDS = {
    "status": "статус", "registration_date": "дата регистрации", "okved": "ОКВЭД",
    "num_court_cases": "арбитражных дел как ответчик", "sum_court_cases": "сумма требований по этим делам",
    "tax_arrears": "налоговая задолженность", "loss_profit_amount": "финансовый результат",
}


@dataclass
class AgentConfig:
    """Параметры одного прогона (CONFIG ноутбука agent_v4_gc3_no_chains)."""

    court_filing_date: date = field(default_factory=date.today)
    llm_max_workers: int = 8
    min_abs_amount_for_llm: float = 0
    rule_skip_older_than_years: int | None = 3
    cluster_homogeneity_threshold: float | None = None
    max_isolated_share: float = 0.30
    max_representatives_per_cluster: int = 10
    max_operations_per_llm_batch: int = 10
    analyze_sparse_clusters: bool = True
    no_cluster_local_clustering: bool = True
    no_cluster_similarity_threshold: float | None = None
    no_cluster_local_clustering_max_ops: int = 4000
    classifier_confidence_min: str = "medium"
    legal_fragment_max_chars: int = 1200
    legal_case_law_queries: int = 1
    legal_normative_queries: int = 2
    legal_select_relevant: bool = True
    legal_selection_max_input_chars: int = 14000
    legal_selection_retries: int = 2
    orchestrator_max_steps: int = 3
    max_guarantee_passes: int = 2
    random_state: int = 42


@dataclass
class DataProfile:
    """Какие необязательные данные есть в выписке (глобальные флаги ноутбука)."""

    has_affiliation: bool = False
    has_business_profile: bool = False
    has_anomaly_scores: bool = False
    clustering_available: bool = False
    local_clustering_done: bool = False


# ───────────────────────── ИНН, даты, значения ─────────────────────────

def normalize_inn(value: Any) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return ""
    try:
        if value is pd.NA or pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    text = re.sub(r"\s", "", str(value))
    if re.fullmatch(r"\d+\.0+", text):
        text = text.split(".")[0]
    if text.lower() in {"nan", "none", "<na>"}:
        return ""
    if text.isdigit() and len(text) in (9, 11):  # ведущий ноль, потерянный при чтении числом
        text = text.zfill(len(text) + 1)
    return text


def parse_dates(values: Any) -> pd.Series:
    """ISO и ДД.ММ.ГГГГ. Общий парсер pandas без формата путает день и месяц в русских датах."""
    series = values if isinstance(values, pd.Series) else pd.Series(values)
    if pd.api.types.is_datetime64_any_dtype(series):
        return series.dt.tz_localize(None) if series.dt.tz is not None else series
    as_text = series.astype("string").str.strip()
    parsed = pd.to_datetime(as_text, format="ISO8601", errors="coerce")
    for fmt in ("%d.%m.%Y", "%d.%m.%Y %H:%M:%S", "%d.%m.%y", "%d/%m/%Y"):
        parsed = parsed.fillna(pd.to_datetime(as_text, format=fmt, errors="coerce"))
    already_datetime = series.map(lambda v: isinstance(v, (datetime, date, pd.Timestamp)))
    if already_datetime.any():
        parsed[already_datetime] = pd.to_datetime(series[already_datetime])
    return parsed


def parse_filing_date(value: Any) -> date:
    """Дата заявления из строки ДД.ММ.ГГГГ / ГГГГ-ММ-ДД / date. ValueError, если не распознана."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    parsed = parse_dates([str(value or "").strip()]).iloc[0]
    if pd.isna(parsed):
        raise ValueError(f"Не удалось разобрать дату заявления: {value!r}. Формат: ДД.ММ.ГГГГ")
    return parsed.date()


def is_missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, (list, dict, tuple, set)):
        return len(value) == 0
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def json_value(value: Any) -> Any:
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        return round(float(value), 2)
    if isinstance(value, (pd.Timestamp, datetime, date)):
        return pd.Timestamp(value).date().isoformat()
    return value


def as_list(value: Any) -> list:
    if value is None:
        return []
    return list(value) if isinstance(value, (list, tuple, set)) else [value]


def normalize_to_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("["):
            try:
                value = json.loads(text)
            except json.JSONDecodeError:
                return [text]
        else:
            return [text] if text else []
    return [str(item) for item in as_list(value) if item]


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


# ───────────────────────── Разбор ответов LLM ─────────────────────────

def message_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(str(b.get("text") or "") if isinstance(b, dict) else str(b) for b in content)
    if isinstance(content, dict):
        return json.dumps(content, ensure_ascii=False, default=str)
    return str(content or "")


def find_json_span(text: str) -> Optional[str]:
    """Первая сбалансированная JSON-структура; для оборванного ответа — хвост от первой скобки."""
    start = next((i for i, ch in enumerate(text) if ch in "{["), None)
    if start is None:
        return None
    depth, in_str, esc = 0, False, False
    for j in range(start, len(text)):
        ch = text[j]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1
            if depth == 0:
                return text[start:j + 1]
    return text[start:]


def looks_truncated(text: str) -> bool:
    span = find_json_span(re.sub(r"```[a-zA-Z0-9_-]*", "", text or "").strip())
    if span is None:
        return not (text or "").strip()
    try:
        json.loads(span)
        return False
    except json.JSONDecodeError:
        return span.count("{") + span.count("[") > span.count("}") + span.count("]")


def extract_json(message: Any) -> Any:
    """dict/list при успехе, иначе {"_parse_error": ...}."""
    if isinstance(message, (dict, list)):
        return message
    content = message if isinstance(message, str) else getattr(message, "content", None)
    if isinstance(content, dict) or (isinstance(content, list) and content and all(
            isinstance(b, dict) and "type" not in b for b in content)):
        return content
    text = re.sub(r"```[a-zA-Z0-9_-]*", "", message_text(content)).strip()
    if not text:
        return {}
    candidates = [text]
    span = find_json_span(text)
    if span:
        candidates += [span, re.sub(r",\s*([}\]])", r"\1", span)]
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, str):
            try:
                parsed = json.loads(parsed)
            except json.JSONDecodeError:
                pass
        return parsed
    logger.warning("Ответ LLM не разобран как JSON (длина %d): %s", len(text), text[:2000])
    return {"_parse_error": text[:500]}


def extract_operations_list(parsed: Any) -> List[dict]:
    if isinstance(parsed, list):
        candidates = parsed
    elif isinstance(parsed, dict):
        candidates = parsed.get("operations")
        if not isinstance(candidates, list):
            candidates = [parsed] if "id" in parsed else next(
                (v for v in parsed.values() if isinstance(v, list) and v and all(isinstance(x, dict) for x in v)), [])
    else:
        candidates = []
    return [op for op in candidates if isinstance(op, dict)]


# ───────────────────────── Очистка текстов для сотрудника ─────────────────────────

_SERVICE_TOKENS = (
    r"anomaly_(?:model_)?score|anomaly_reasons|atypicality(?:_score|_reasons)?|top_drivers|purpose_cleaned|interval_days|"
    r"cluster(?:_id)?|idx|risk_score|risk_grade|s_i|proximity|recency|operation_type|requested_documents|"
    r"counterparty_id|counterparty_profile|tool_results?|legal_materials|legal_refs|practice_refs|"
    r"get_conterparty_risk|search_practice_and_normative|risk_level"
)
_PAREN_WITH_SERVICE_RE = re.compile(rf"\s*\([^()]*\b(?:{_SERVICE_TOKENS})\b[^()]*\)", re.IGNORECASE)
_SERVICE_RE = re.compile(rf"\b(?:{_SERVICE_TOKENS})\b(?:\s*[:=≈]?\s*-?\d+(?:[.,]\d+)?)?", re.IGNORECASE)
_CLUSTER_RU_RE = re.compile(r"\s*\(?\b(?:в\s+)?кластер\w*(?:\s+№?\s*-?\d+)?\)?", re.IGNORECASE)
_ORG_RE = re.compile(r"\bORG_\d+\b")
_CODE_LABELS = {**HYPOTHESIS_LABELS, **RISK_FACTORS, **{k: v[0].lower() for k, v in ROUTES.items() if k != "none"}}
_CODE_RE = re.compile(r"\b(" + "|".join(map(re.escape, sorted(_CODE_LABELS, key=len, reverse=True))) + r")\b")


class SanitizeCounter:
    count = 0


def sanitize_text(value: Any) -> str:
    """Убирает из текста для сотрудника служебные поля, скоры, коды и псевдонимы."""
    if is_missing(value):
        return ""
    text = str(value)
    cleaned = _PAREN_WITH_SERVICE_RE.sub("", text)
    cleaned = _SERVICE_RE.sub("", cleaned)
    cleaned = _CLUSTER_RU_RE.sub("", cleaned)
    cleaned = _ORG_RE.sub("контрагент", cleaned)
    cleaned = _CODE_RE.sub(lambda m: _CODE_LABELS[m.group(1)], cleaned)
    cleaned = re.sub(r"\s+([,.;:])", r"\1", re.sub(r"\s{2,}", " ", cleaned)).strip()
    if cleaned != text:
        SanitizeCounter.count += 1
    return cleaned


def str_list(value: Any, limit: int | None = None) -> List[str]:
    items = [sanitize_text(v) for v in normalize_to_list(value)]
    items = [v for v in items if v]
    return items[:limit] if limit else items


def to_risk_level(value: Any) -> Optional[int]:
    try:
        level = int(float(value))
    except (TypeError, ValueError):
        return None
    return level if level in (0, 1) else None


def truncate_long_strings(value: Any, max_chars: int) -> Any:
    if isinstance(value, str):
        return value if len(value) <= max_chars else value[:max_chars] + "…"
    if isinstance(value, dict):
        return {k: truncate_long_strings(v, max_chars) for k, v in value.items()}
    if isinstance(value, list):
        return [truncate_long_strings(v, max_chars) for v in value]
    return value


def counterparty_side(record: dict) -> str | None:
    direction = record.get("direction")
    if direction == "убытие":
        return "credit"
    if direction == "прибытие":
        return "debit"
    return None


def resolve_counterparty_inn(record: dict | pd.Series) -> str | None:
    """ИНН контрагента по направлению: при списании — получатель, при поступлении — плательщик."""
    record = record if isinstance(record, dict) else record.to_dict()
    side = counterparty_side(record)
    for s in ([side] if side else ["credit", "debit"]):
        inn = normalize_inn(record.get(f"{s}_inn"))
        if inn.isdigit() and len(inn) in (10, 12):
            return inn
    return None


def resolve_counterparty_name(record: dict | pd.Series) -> str | None:
    """Наименование контрагента по тому же правилу направления, что и resolve_counterparty_inn."""
    record = record if isinstance(record, dict) else record.to_dict()
    side = counterparty_side(record)
    for s in ([side] if side else ["credit", "debit"]):
        name = record.get(f"{s}_name")
        name = "" if is_missing(name) else str(name).strip()
        if name:
            return name
    return None
