"""Тип операции: классификатор по каталогу (LLM), открытая классификация и детерминированный режим без LLM.

1. Классификатор по каталогу CATALOG (предмет subject_id × действие action_id × флаги). В LLM уходят только
   уникальные пары (очищенное назначение, направление). Тип строится кодом, документы — из OPERATION_MATRIX.
2. Открытая классификация — для операций вне каталога и с низкой уверенностью.
3. Без LLM: сопоставление назначения с формулировками каталога (по основам слов).
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Callable

import pandas as pd

from .catalog import (ALL_FLAGS, CATALOG, CATALOG_ACTIONS, CLASSIFIER_SYSTEM_PROMPT, OPEN_TYPE_SYSTEM_PROMPT,
                      OPERATION_MATRIX, OPERATION_TYPE_BY_ACTION, OPERATION_TYPE_BY_SUBJECT, PAYMENT_DOC, UNKNOWN_TYPES, document_reference, resolve_documents,
                      resolve_operation_type, with_document_reference)
from .common import extract_json, extract_operations_list, normalize_to_list
from .runtime import Runtime, invoke_llm, make_messages, run_batches_parallel

logger = logging.getLogger("finapp.agent.classifier")

CLASSIFIER_MAX_OPS_PER_BATCH = 20
_CONF_ORDER = {"low": 0, "medium": 1, "high": 2}
UNSETTLED_TYPE = "не установлено по доступным данным"
SUBJECT_GROUP = {subject: group for group, subjects in CATALOG["subjects"].items() for subject in subjects}

# Название типа операции -> группа каталога (для типов, выбранных открытой классификацией из стандартных).
TYPE_GROUP = {name: SUBJECT_GROUP[subject] for subject, name in OPERATION_TYPE_BY_SUBJECT.items() if subject in SUBJECT_GROUP}
TYPE_GROUP.update({name: SUBJECT_GROUP[subject] for (subject, _, _), name in OPERATION_TYPE_BY_ACTION.items()
                   if subject in SUBJECT_GROUP})

# Группа каталога -> категория для сотрудника (вкладки «Обзор» и «Операции»).
GROUP_CATEGORY = {
    "labor": "Заработная плата и выплаты персоналу",
    "goods_material": "Поставка товаров, работ, услуг", "works": "Поставка товаров, работ, услуг",
    "services": "Поставка товаров, работ, услуг",
    "finance": "Займы, кредиты и финансирование",
    "budget": "Налоги и сборы",
    "intragroup_cash": "Внутригрупповые и наличные операции",
    "bank_services": "Банковские услуги",
    "retail_payments": "Выручка и расчёты с покупателями",
    "unilateral_enforcement": "Принудительные платежи",
    "other": "Прочее",
}


def _to_catalog_direction(direction: Any) -> str:
    return "inflow" if str(direction).strip().lower() in ("прибытие", "inflow") else "outflow"


def _classification_key(row: pd.Series) -> tuple:
    cleaned = str(row.get("purpose_cleaned") or "").strip() or " ".join(str(row["purpose"] or "").lower().split())
    return cleaned, _to_catalog_direction(row["direction"])


# ───────────────────────── Режим без LLM ─────────────────────────

def _stems(phrase: str) -> list[str]:
    words = re.findall(r"[\wёЁ]+", phrase.lower())
    return [w if len(w) <= 4 else w[:max(4, len(w) - 2)] for w in words]


_TRIGGER_INDEX: list[tuple[str, str, list[str]]] | None = None


def _trigger_index() -> list[tuple[str, str, list[str]]]:
    global _TRIGGER_INDEX
    if _TRIGGER_INDEX is None:
        index = []
        for subjects in CATALOG["subjects"].values():
            for subject, spec in subjects.items():
                if subject == "unknown":
                    continue
                for trigger in spec["triggers"]:
                    index.append((subject, trigger, _stems(trigger)))
        _TRIGGER_INDEX = index
    return _TRIGGER_INDEX


_RKO_RE = re.compile(r"расч[её]тно[- ]кассов\w*\s+обслуживан\w*")
_CASH_RE = re.compile(r"сняти\w*\s+наличн|получени\w*\s+наличн|выдач\w*\s+наличн")


def keyword_subject(purpose: str) -> str | None:
    """Предмет операции по формулировкам каталога: побеждает самое длинное совпадение (по основам слов)."""
    text = str(purpose).lower()
    if _CASH_RE.search(text):
        return "cash_withdrawal"
    text = _RKO_RE.sub("рко", text)
    words = re.findall(r"[\wёЁ]+", text)
    best, best_score = None, 0
    for subject, trigger, stems in _trigger_index():
        if not stems:
            continue
        if all(any(w.startswith(s) if len(s) > 3 else w == s for w in words) for s in stems):
            score = sum(len(s) for s in stems)
            if score > best_score:
                best, best_score = subject, score
    return best


def keyword_action(subject: str, purpose: str, direction: str) -> str | None:
    text = purpose.lower()
    allowed = CATALOG_ACTIONS.get(subject, [])
    if subject in ("loan", "credit", "financial_aid"):
        if re.search(r"процент", text) and "interest_payment" in allowed:
            return "interest_payment"
        if re.search(r"возврат|погашен", text):
            return "repayment" if "repayment" in allowed else ("return" if "return" in allowed else None)
        if re.search(r"предоставлен|выдач|получен|перечислен", text):
            return "provision" if direction == "outflow" or "receipt" not in allowed else "receipt"
    if subject in ("vat", "ndfl", "profit_tax", "insurance_contributions", "single_tax_payment") and direction == "inflow":
        return "tax_refund" if "tax_refund" in allowed else None
    preferred = "payment" if direction == "outflow" else "receipt"
    if preferred in allowed:
        return preferred
    if direction == "outflow" and "tax_payment" in allowed:
        return "tax_payment"
    return allowed[0] if allowed else None


def classify_by_rules(frame: pd.DataFrame) -> pd.DataFrame:
    records = []
    for _, row in frame.iterrows():
        purpose = str(row["purpose"] or "")
        direction = _to_catalog_direction(row["direction"])
        subject = keyword_subject(purpose) or "unknown"
        action = keyword_action(subject, purpose, direction) if subject != "unknown" else None
        op_type = resolve_operation_type(subject, action, [], direction)
        records.append({
            "idx": str(row["idx"]), "operation_type": op_type,
            "requested_documents": resolve_documents(op_type, action, [], direction),
            "operation_type_confidence": "medium" if subject != "unknown" else "low",
            "operation_type_source": "rules", "direction_consistent": True,
            "operation_group": SUBJECT_GROUP.get(subject, "other"),
        })
    return _finish(pd.DataFrame(records), frame)


# ───────────────────────── LLM-классификатор ─────────────────────────

def _call_llm_batch(rt: Runtime, system_prompt: str, batch: list[dict]) -> dict[str, dict]:
    """Пакет [{idx, purpose, direction}] с локальными номерами -> {исходный idx: ответ}."""
    local = {str(i): item for i, item in enumerate(batch, start=1)}
    payload = {"operations": [{**item, "idx": i} for i, item in local.items()]}
    response = invoke_llm(rt.helper, make_messages(system_prompt, json.dumps(payload, ensure_ascii=False)), role="classifier")
    out = {}
    for op in extract_operations_list(extract_json(response)):
        item = local.get(str(op.get("idx")))
        if item is not None:
            out[item["idx"]] = op
    if len(out) < len(batch):
        logger.warning("Классификация: ответ по %d из %d операций пакета.", len(out), len(batch))
    return out


def _classify_unique(rt: Runtime, unique_rows: pd.DataFrame, system_prompt: str, desc: str,
                     on_done: Callable[[int, int], None] | None) -> dict[str, dict]:
    payload = [{"idx": str(r["idx"]), "purpose": str(r["purpose"]), "direction": _to_catalog_direction(r["direction"])}
               for _, r in unique_rows.iterrows()]
    batches = [payload[i:i + CLASSIFIER_MAX_OPS_PER_BATCH] for i in range(0, len(payload), CLASSIFIER_MAX_OPS_PER_BATCH)]
    answers: dict[str, dict] = {}
    for part in run_batches_parallel(batches, lambda b: _call_llm_batch(rt, system_prompt, b), desc,
                                     rt.cfg.llm_max_workers, on_done=on_done):
        answers.update(part or {})
    return answers


def classify_operation_types(rt: Runtime, frame: pd.DataFrame,
                             on_done: Callable[[int, int], None] | None = None) -> pd.DataFrame:
    if not rt.use_llm:
        return classify_by_rules(frame)
    conf_min = _CONF_ORDER.get(rt.cfg.classifier_confidence_min, 1)
    rows = frame[["idx", "purpose", "direction", "purpose_cleaned"]].copy()
    rows["_key"] = rows.apply(_classification_key, axis=1)
    unique = rows.drop_duplicates("_key")
    logger.info("Классификатор: %d уникальных назначений из %d операций.", len(unique), len(rows))

    answers = _classify_unique(rt, unique, CLASSIFIER_SYSTEM_PROMPT, "Классификация типов", on_done)
    by_key = {key: answers.get(idx, {}) for key, idx in zip(unique["_key"], unique["idx"].astype(str))}

    records = []
    for _, row in rows.iterrows():
        answer = by_key.get(row["_key"], {})
        subject = answer.get("subject_id") if answer.get("subject_id") in CATALOG_ACTIONS else "unknown"
        action = answer.get("action_id") if answer.get("action_id") in CATALOG_ACTIONS[subject] else None
        flags = [f for f in normalize_to_list(answer.get("flags")) if f in ALL_FLAGS]
        direction = row["_key"][1]
        confidence = str(answer.get("confidence") or "low").lower()
        op_type = resolve_operation_type(subject, action, flags, direction)
        records.append({
            "idx": str(row["idx"]), "operation_type": op_type,
            "requested_documents": resolve_documents(op_type, action, flags, direction),
            "operation_type_confidence": confidence, "operation_type_source": "catalog",
            "direction_consistent": answer.get("direction_consistent", True) is not False,
            "operation_group": SUBJECT_GROUP.get(subject, "other"), "_key": row["_key"],
        })
    result = pd.DataFrame(records)

    # Открытая классификация: вне каталога или неуверенно.
    open_mask = result["operation_type"].eq("Неопознанный платёж") | (
        result["operation_type_confidence"].map(lambda c: _CONF_ORDER.get(c, 0)) < conf_min)
    if open_mask.any():
        open_unique = rows[rows["idx"].astype(str).isin(result.loc[open_mask, "idx"])].drop_duplicates("_key")
        open_answers = _classify_unique(rt, open_unique, OPEN_TYPE_SYSTEM_PROMPT, "Открытая классификация", on_done)
        open_by_key = {key: open_answers.get(idx, {}) for key, idx in zip(open_unique["_key"], open_unique["idx"].astype(str))}
        for pos in result.index[open_mask]:
            answer = open_by_key.get(result.at[pos, "_key"], {})
            guess = str(answer.get("operation_type") or "").strip()
            if not guess or guess == UNSETTLED_TYPE:
                continue
            is_standard = guess in OPERATION_MATRIX
            docs = (resolve_documents(guess, None, [], result.at[pos, "_key"][1]) if is_standard
                    else [str(d) for d in normalize_to_list(answer.get("requested_documents"))][:5])
            result.at[pos, "operation_type"] = guess
            result.at[pos, "requested_documents"] = docs or [PAYMENT_DOC]
            result.at[pos, "operation_type_source"] = "open_standard" if is_standard else "open_new_type"
            result.at[pos, "operation_type_confidence"] = "medium"
    return _finish(result.drop(columns="_key"), frame)


def _finish(result: pd.DataFrame, frame: pd.DataFrame) -> pd.DataFrame:
    """Ссылка на документ из назначения, флаг «тип не установлен», категория для сотрудника."""
    result = result.reset_index(drop=True)
    purposes = frame.set_index(frame["idx"].astype(str))["purpose"]
    for pos in result.index:
        purpose = str(purposes[result.at[pos, "idx"]])
        if result.at[pos, "operation_type"] == "Неопознанный платёж" and document_reference(purpose):
            result.at[pos, "operation_type"] = "Неопознанный платёж (ссылка на документ)"
            result.at[pos, "requested_documents"] = list(
                OPERATION_MATRIX["Неопознанный платёж (ссылка на документ)"]["requested_documents"])
        if result.at[pos, "operation_type_source"] not in ("catalog", "rules") or result.at[pos, "operation_type"] in UNKNOWN_TYPES:
            result.at[pos, "requested_documents"] = with_document_reference(result.at[pos, "requested_documents"], purpose)
    result["operation_type_need_review"] = result["operation_type"].str.startswith("Неопознанный")
    group = result["operation_group"].where(result["operation_group"].ne("other"),
                                            result["operation_type"].map(TYPE_GROUP)).fillna("other")
    result["operation_group"] = group
    result["operation_category"] = result["operation_group"].map(GROUP_CATEGORY).fillna("Прочее")
    result.loc[result["operation_type_need_review"], "operation_category"] = "Не классифицировано"
    return result
