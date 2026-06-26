from __future__ import annotations

import re
from typing import Any

from .fallback import recommendation_for, recommended_documents
from .utils import clean_text, ensure_list, parse_maybe_json, short_string, unique_strings
def extract_connection_basis(row: dict[str, Any]) -> dict[str, Any]:
    raw = row.get("connections_basis", row.get("connection_basis", {}))
    parsed = parse_maybe_json(raw)
    if isinstance(parsed, dict):
        return {
            "strongest_connection": clean_text(parsed.get("strongest_connection", "")),
            "connection_set_summary": clean_text(parsed.get("connection_set_summary", "")),
            "connection_strength": clean_text(parsed.get("connection_strength", "")),
            "influence_on_risk": clean_text(parsed.get("influence_on_risk", "")),
            "limitation": clean_text(parsed.get("limitation", "")),
        }
    text = clean_text(parsed)
    if text:
        return {
            "strongest_connection": text[:220],
            "connection_set_summary": text,
            "connection_strength": infer_connection_strength_from_text(text),
            "influence_on_risk": "Связь учтена как сигнал из output агента.",
            "limitation": "Структура связи передана строкой, а не объектом connections_basis.",
        }
    # fallback to source graph text
    desc = clean_text(row.get("collect_all_graph_connections.description"))
    if desc:
        strength = infer_connection_strength_from_text(desc)
        return {
            "strongest_connection": desc[:220],
            "connection_set_summary": desc,
            "connection_strength": strength,
            "influence_on_risk": "Связь учтена как входной сигнал; требуется подтверждение.",
            "limitation": "Связь взята из входного поля, а не из финального вывода анализатора.",
        }
    return {
        "strongest_connection": "не установлена",
        "connection_set_summary": "Связь сторон не передана или не выявлена.",
        "connection_strength": "не установлена",
        "influence_on_risk": "не влияет",
        "limitation": "нет данных о связи",
    }
def extract_challenge_criteria(row: dict[str, Any]) -> dict[str, Any]:
    raw = parse_maybe_json(row.get("challenge_criteria", {}))
    if isinstance(raw, dict):
        return {
            "potential_route": clean_text(raw.get("potential_route", "")),
            "criteria_matched": ensure_list(raw.get("criteria_matched", [])),
            "criteria_missing": ensure_list(raw.get("criteria_missing", [])),
            "documents_needed": ensure_list(raw.get("documents_needed", [])),
            "challenge_readiness": clean_text(raw.get("challenge_readiness", "")),
        }
    return {
        "potential_route": "не установлено",
        "criteria_matched": [],
        "criteria_missing": [],
        "documents_needed": [],
        "challenge_readiness": "не установлена",
    }
def recommendation_summary(recommendation_obj: Any, row: dict[str, Any], category: str, risk_level_value: int) -> str:
    if isinstance(recommendation_obj, dict):
        summary = clean_text(recommendation_obj.get("summary"))
        goal = clean_text(recommendation_obj.get("verification_goal"))
        if summary and goal:
            return f"{summary} Цель проверки: {goal}"
        if summary:
            return summary
    text = clean_text(recommendation_obj)
    if text:
        return text
    return recommendation_for(category, risk_level_value)
def documents_from_output(recommendation_obj: Any, challenge: dict[str, Any], category: str, risk_level_value: int) -> list[str]:
    docs = []
    if isinstance(recommendation_obj, dict):
        docs.extend(ensure_list(recommendation_obj.get("documents_to_request", [])))
    docs.extend(ensure_list(challenge.get("documents_needed", [])))
    if not docs:
        docs.extend(recommended_documents(category, risk_level_value))
    return unique_strings(docs)
def connection_summary(connection: dict[str, Any]) -> str:
    strength = normalize_connection_strength(connection.get("connection_strength"))
    strongest = clean_text(connection.get("strongest_connection"))
    influence = clean_text(connection.get("influence_on_risk"))
    if strongest and strongest != "не установлена":
        return f"{strength}: {short_string(strongest, 120)}"
    if influence:
        return short_string(f"{strength}: {influence}", 120)
    return strength or "не установлена"
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
def infer_connection_strength_from_text(text: str) -> str:
    t = text.lower()
    if any(token in t for token in ["не выяв", "не установ", "нет связи", "связь отсутств"]):
        return "не установлена"
    if any(token in t for token in ["бенефициар", "учредител", "руководител", "директор", "родствен", "аффилир", "группа"]):
        return "сильная"
    if any(token in t for token in ["общий", "адрес", "телефон", "ip", "цепоч"]):
        return "средняя"
    return "слабая" if text.strip() else "не установлена"
def legal_route(qualification: Any) -> str:
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
def detail_key_for_route(route: str) -> str:
    r = route.lower()
    if "61.3" in r:
        return "route_613"
    if "61.2" in r:
        return "route_612"
    if "низ" in r or "не установ" in r:
        return "low"
    return "medium"
def readiness_from_risk(risk_level_value: int) -> str:
    if risk_level_value >= 3:
        return "высокая после подтверждения документов"
    if risk_level_value == 2:
        return "средняя"
    return "низкая"
