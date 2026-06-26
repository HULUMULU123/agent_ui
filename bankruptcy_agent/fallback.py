from __future__ import annotations

from typing import Any

import re

import numpy as np
import pandas as pd

from .utils import clean_text, format_date, format_money, short_string

def legal_route(qualification: Any) -> str:
    """Локальный fallback для определения правового маршрута без импорта extraction.

    Дублирует легкую версию функции из extraction.py, чтобы не создавать
    циклическую зависимость fallback -> extraction -> fallback.
    """
    q = clean_text(qualification).lower()
    if re.search(r"61\s*\.\s*3|предпочт", q):
        return "ст. 61.3 — предпочтительное удовлетворение"
    if re.search(r"61\s*\.\s*2|неравноцен|вред", q):
        return "ст. 61.2 — вред / неравноценность"
    if "экономическ" in q or "документ" in q:
        return "проверка экономического смысла"
    if "не установ" in q or not q:
        return "признаки оспоримости не установлены"
    return short_string(clean_text(qualification), 80)


def normalize_connection_strength(value: Any) -> str:
    text = clean_text(value).lower()
    if not text:
        return "не установлена"
    if any(token in text for token in ["силь", "высок", "strong"]):
        return "сильная"
    if any(token in text for token in ["сред", "medium"]):
        return "средняя"
    if any(token in text for token in ["слаб", "низ", "weak", "low"]):
        return "слабая"
    if "не установ" in text or "нет" == text:
        return "не установлена"
    return clean_text(value)


def connection_summary(connection: dict[str, Any]) -> str:
    strength = normalize_connection_strength(connection.get("connection_strength"))
    strongest = clean_text(connection.get("strongest_connection"))
    influence = clean_text(connection.get("influence_on_risk"))
    if strongest and strongest != "не установлена":
        return f"{strength}: {short_string(strongest, 120)}"
    if influence:
        return short_string(f"{strength}: {influence}", 120)
    return strength or "не установлена"


def readiness_from_risk(risk_level_value: int) -> str:
    if risk_level_value >= 3:
        return "высокая после подтверждения документов"
    if risk_level_value == 2:
        return "средняя"
    return "низкая"



def deterministic_operation_analysis(sampled_df: pd.DataFrame) -> pd.DataFrame:
    """Детерминированный fallback в формате analyzer_result.operations.

    Не заменяет юридический вывод. Нужен для тестового UI и слабых устройств,
    когда LLM/runtime объекты тетрадки недоступны.
    """
    rows: list[dict[str, Any]] = []
    if sampled_df.empty:
        return pd.DataFrame(rows)

    amount_abs = sampled_df["amount"].abs()
    q75 = amount_abs.quantile(0.75) if len(amount_abs) else 0
    q90 = amount_abs.quantile(0.90) if len(amount_abs) else 0

    for _, tx in sampled_df.iterrows():
        purpose = str(tx.get("purpose", "")).lower()
        amount = float(tx.get("amount", 0) or 0)
        anomaly = float(tx.get("anomaly_score", 0) or 0)
        model_score = float(tx.get("anomaly_model_score", anomaly) or 0)
        category = classify_transaction_category(purpose, amount)
        risk_level_value = infer_risk_level(purpose, abs(amount), anomaly, model_score, q75, q90)
        qualification = qualification_for(category, risk_level_value)
        connection = fallback_connection_basis(tx, risk_level_value)
        challenge = fallback_challenge_criteria(category, risk_level_value, qualification)
        docs = recommended_documents(category, risk_level_value)
        counterparty_name = str(tx.get("credit_name") or tx.get("debit_name") or "Контрагент не определен")
        counterparty_inn = str(tx.get("credit_inn") or tx.get("debit_inn") or "")
        rows.append({
            "cluster_id": int(tx.get("cluster", 0) or 0),
            "idx": str(tx.get("idx", "")),
            "date": format_date(tx.get("date")),
            "interval": str(tx.get("interval", "")),
            "transaction_category": category,
            "amount": amount,
            "counterparty": counterparty_name,
            "counterparty_inn": counterparty_inn,
            "counterparty_category": infer_counterparty_category(counterparty_name, counterparty_inn),
            "connections_basis": connection,
            "connection_summary": connection_summary(connection),
            "connection_strength": normalize_connection_strength(connection.get("connection_strength")),
            "challenge_criteria": challenge,
            "challenge_readiness": challenge.get("challenge_readiness", readiness_from_risk(risk_level_value)),
            "risk_level": int(risk_level_value),
            "risk_label": risk_label(risk_level_value),
            "legal_qualification": qualification,
            "legal_route": legal_route(qualification),
            "legal_basis": [qualification] if qualification != "не установлена" else [],
            "court_basis": [],
            "decision_argumentation": build_reasoning(tx, category, risk_level_value, connection),
            "risk_explanation": build_risk_explanation(tx, category, risk_level_value),
            "recommendation": recommendation_for(category, risk_level_value),
            "recommended_documents": docs,
            "used_tools": [],
            "status": "fallback_without_llm",
            "error": "",
        })
    return pd.DataFrame(rows)
def classify_transaction_category(purpose: str, amount: float) -> str:
    p = purpose.lower()
    rules = [
        ("возврат займа", ["возврат", "займ"]),
        ("займ / кредит", ["займ", "кредит", "процент"]),
        ("налоги и обязательные платежи", ["налог", "ндс", "нпд", "фнс", "пеня", "страхов"]),
        ("зарплата и персонал", ["зарплат", "аванс", "преми", "сотруд", "персонал"]),
        ("аренда", ["аренд"]),
        ("поставка / подряд", ["договор", "счет", "постав", "подряд", "услуг", "работ"]),
        ("внутригрупповой перевод", ["перевод", "пополнение", "между счет"]),
    ]
    for label, keys in rules:
        if any(key in p for key in keys):
            return label
    return "прочая операция" if amount >= 0 else "прочее списание"
def infer_risk_level(purpose: str, amount_abs: float, anomaly: float, model_score: float, q75: float, q90: float) -> int:
    p = purpose.lower()
    score = 0
    if max(anomaly, model_score) >= 0.80:
        score += 2
    elif max(anomaly, model_score) >= 0.55:
        score += 1
    if q90 and amount_abs >= q90:
        score += 2
    elif q75 and amount_abs >= q75:
        score += 1
    if any(k in p for k in ["займ", "возврат", "без договора", "уступка", "цессион", "аффилир", "взаимозач", "перевод"]):
        score += 1
    if any(k in p for k in ["налог", "зарплат", "аренд"]):
        score -= 1
    if score <= 0:
        return 0
    if score == 1:
        return 1
    if score in (2, 3):
        return 2
    return 3
def qualification_for(category: str, risk_level_value: int) -> str:
    if risk_level_value <= 1:
        return "признаков оспоримости по доступным данным не установлено"
    if category in {"возврат займа", "займ / кредит"}:
        return "потенциальная проверка по ст. 61.3 127-ФЗ: предпочтительное удовлетворение"
    if category in {"внутригрупповой перевод", "прочая операция", "прочее списание"}:
        return "потенциальная проверка по ст. 61.2 127-ФЗ: неравноценность или причинение вреда"
    return "потенциальная проверка экономического смысла операции и документов основания"
def recommendation_for(category: str, risk_level_value: int) -> str:
    if risk_level_value <= 1:
        return "Сохранить операцию в мониторинге; дополнительные документы запрашивать только при споре по основанию платежа."
    docs = ", ".join(recommended_documents(category, risk_level_value))
    return f"Запросить документы: {docs}. Проверить реальность обязательства, связь сторон и рыночность условий."
def recommended_documents(category: str, risk_level_value: int) -> list[str]:
    if risk_level_value <= 1:
        return []
    base = ["договор", "счет", "акт/накладная", "переписка по основанию платежа"]
    if category in {"возврат займа", "займ / кредит"}:
        return ["договор займа", "график платежей", "подтверждение выдачи займа", "расчет задолженности"]
    if category == "внутригрупповой перевод":
        return ["договорное основание перевода", "расшифровка взаиморасчетов", "документы о связи сторон"]
    return base
def fallback_connection_basis(tx: pd.Series, risk_level_value: int) -> dict[str, Any]:
    raw = str(tx.get("collect_all_graph_connections.description", "") or "").strip()
    text = raw.lower()
    if not raw or any(token in text for token in ["не выяв", "не установ", "нет связи", "связь отсутств"]):
        return {
            "strongest_connection": "не установлена по входным данным",
            "connection_set_summary": "Графовые связи по операции не переданы.",
            "connection_strength": "не установлена",
            "influence_on_risk": "Не влияет на риск без подтвержденной связи.",
            "limitation": "Fallback-режим не выполняет внешний графовый поиск.",
        }
    strong_tokens = ["бенефициар", "учредител", "руководител", "директор", "родствен", "аффилир", "группа"]
    strength = "сильная" if any(t in text for t in strong_tokens) else "средняя"
    influence = "усиливает проверочную гипотезу, но не является самостоятельным доказательством" if risk_level_value >= 2 else "учитывается как сигнал, но не повышает риск без иных подтверждений"
    return {
        "strongest_connection": raw[:220],
        "connection_set_summary": raw[:420],
        "connection_strength": strength,
        "influence_on_risk": influence,
        "limitation": "Связь взята из входной колонки collect_all_graph_connections.description; требуется проверка источника.",
    }
def fallback_challenge_criteria(category: str, risk_level_value: int, qualification: str) -> dict[str, Any]:
    route = legal_route(qualification)
    if risk_level_value <= 1:
        return {
            "potential_route": route,
            "criteria_matched": ["обычная хозяйственная версия не опровергнута"],
            "criteria_missing": ["не установлены признаки самостоятельной оспоримости"],
            "documents_needed": [],
            "challenge_readiness": "низкая",
        }
    return {
        "potential_route": route,
        "criteria_matched": ["операция выделена по сумме/аномальности/назначению", "требуется проверка периода и основания платежа"],
        "criteria_missing": ["документы основания", "подтверждение реальности обязательства", "проверка связи сторон"],
        "documents_needed": recommended_documents(category, risk_level_value),
        "challenge_readiness": "средняя" if risk_level_value == 2 else "высокая после подтверждения документов",
    }
def infer_counterparty_category(name: str, inn: str) -> str:
    text = f"{name} {inn}".lower()
    digits = "".join(filter(str.isdigit, inn))
    if "ип" in text or len(digits) == 12:
        return "ИП / физическое лицо"
    if "ооо" in text or len(digits) == 10:
        return "юридическое лицо"
    return "не определено"
def risk_label(value: int) -> str:
    return {0: "0 / низкий", 1: "1 / умеренный", 2: "2 / средний", 3: "3 / высокий"}.get(int(value), "не определен")
def risk_class(value: int) -> str:
    if value <= 1:
        return "green"
    if value == 2:
        return "yellow"
    return "red"
def build_reasoning(tx: pd.Series, category: str, risk_level_value: int, connection: dict[str, Any] | None = None) -> str:
    amount = format_money(tx.get("amount", 0))
    connection_text = connection_summary(connection or {})
    return f"Категория: {category}; сумма: {amount}; интервал: {tx.get('interval', 'неизвестно')}; связь: {connection_text}; технический уровень риска: {risk_level_value}."
def build_risk_explanation(tx: pd.Series, category: str, risk_level_value: int) -> str:
    if risk_level_value <= 1:
        return "Операция не выделяется как высокорисковая по доступным полям; требуется стандартная проверка основания."
    return "Операция требует документальной проверки: учитываются сумма, аномальность, период, связь сторон и тип назначения платежа."
