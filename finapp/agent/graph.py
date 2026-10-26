"""Граф агента: оркестратор → (инструменты → оркестратор)* → анализатор, постобработка ответа.

Операции внутри пакета получают короткие id "1".."N"; соответствие с реальным idx хранит код, поэтому модель
не может перепутать длинные хеш-идентификаторы. Контрагенты передаются псевдонимами ORG_N; реальный ИНН
подставляется только при вызове get_conterparty_risk.

Ответ анализатора проходит finalize_operation: risk_level остаётся тем, что поставила модель; маршрут
согласуется с уровнем риска, текст маршрута и статья подставляются кодом; legal_refs/practice_refs разворачиваются
только в реально найденные материалы; из текстов вычищаются служебные поля, скоры, коды и псевдонимы.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List

import pandas as pd

from .common import (AFFILIATION_COLUMNS, PROFILE_FIELDS, DataProfile, counterparty_side, dumps,
                     extract_json, extract_operations_list, is_missing, json_value, looks_truncated, message_text,
                     normalize_to_list, resolve_counterparty_inn, sanitize_text, str_list, to_risk_level)
from .prompts import (LOW_RISK_ROUTES, RISK_FACTORS, ROUTE_CHECKS, ROUTES,
                      build_system_prompts)
from .runtime import Runtime, invoke_llm, make_messages
from .tools import TOOL_NAMES, run_tool

logger = logging.getLogger("finapp.agent.graph")

JSON_REPAIR_SYSTEM_PROMPT = """
Исправь структуру JSON. На входе ответ, который должен быть объектом {"operations": [...]}, но повреждён:
лишний текст, markdown, оборванный конец, лишние запятые, неэкранированные кавычки.
Верни только валидный JSON этой формы. Сохрани содержимое и поле id каждой операции как есть.
Ничего не добавляй и не придумывай; операцию, которую нельзя восстановить, отбрось.
"""

_ROUTE_BY_FACTOR = [
    ("debt_repayment", "preference"), ("transit", "transit"), ("no_counter_performance", "asset_withdrawal"),
    ("cash_out", "asset_withdrawal"), ("counterparty_mismatch", "formal_deal"),
    ("unclear_outflow", "counter_performance"), ("related_party", "asset_withdrawal"),
    ("tool_question", "counter_performance"),
]


# ───────────────────────── Представление операции для модели ─────────────────────────

def to_llm_operation(record: dict, local_id: str, counterparty_alias: str | None, profile: DataProfile) -> dict:
    """Операция в том виде, в каком её видит модель: без служебных полей, скоров и реальных ИНН."""
    amount = pd.to_numeric(record.get("amount"), errors="coerce")
    op = {
        "id": local_id,
        "date": json_value(record["date"]) if not is_missing(record.get("date")) else "не указана",
        "period": record.get("period") or "не определено",
        "direction": record.get("direction") or "не определено",
        "amount": round(abs(float(amount)), 2) if not pd.isna(amount) else None,
        "purpose": str(record.get("purpose") or ""),
        "operation_type": record.get("operation_type") or "Неопознанный платёж",
        "requested_documents": list(record["requested_documents"]) if isinstance(
            record.get("requested_documents"), (list, tuple)) else [],
        "counterparty_id": counterparty_alias or "не установлен",
    }
    if profile.has_anomaly_scores:
        for column in ("anomaly_score", "anomaly_model_score"):
            value = record.get(column)
            op[column] = None if is_missing(value) else json_value(value)
        if not is_missing(record.get("atypicality_reasons")):
            op["anomaly_reasons"] = str(record["atypicality_reasons"])
    if (profile.clustering_available or profile.local_clustering_done) and not is_missing(record.get("cluster")):
        op["cluster"] = int(record["cluster"])
    if profile.has_affiliation:
        relations = next((record.get(c) for c in AFFILIATION_COLUMNS if not is_missing(record.get(c))), None)
        op["relations"] = relations if relations is not None else "не переданы"
    if profile.has_business_profile:
        side = counterparty_side(record)
        prof = {label: json_value(record.get(f"{side}_{field}"))
                for field, label in PROFILE_FIELDS.items()
                if side and not is_missing(record.get(f"{side}_{field}"))}
        op["counterparty_profile"] = prof or "нет сведений"
    return op


def llm_identity_key(record: dict, profile: DataProfile) -> str:
    """Операции с одинаковым видом для модели и одним контрагентом получают один и тот же ответ."""
    op = to_llm_operation(record, "", None, profile)
    op.pop("id")
    op.pop("cluster", None)
    return json.dumps([op, resolve_counterparty_inn(record)], ensure_ascii=False, sort_keys=True, default=str)


# ───────────────────────── Постобработка ответа анализатора ─────────────────────────

def _resolve_refs(value: Any, materials: Dict[str, str], note_key: str, note_label: str) -> List[str]:
    """[{"ref": "N1", "application": "..."}] или ["N1"] -> тексты найденных материалов с пояснением модели."""
    out = []
    for item in value if isinstance(value, list) else normalize_to_list(value):
        ref, note = (item.get("ref"), item.get(note_key)) if isinstance(item, dict) else (item, None)
        if str(ref) in materials:
            note = sanitize_text(note)
            out.append(materials[str(ref)] + (f"\n{note_label}: {note}" if note else ""))
    return out


def _connections_text(value: Any, profile: DataProfile) -> str:
    if not profile.has_affiliation:
        return "Связи сторон в этой выгрузке не передавались."
    if isinstance(value, dict):
        labels = {"strongest_connection": "Сильнейшая связь", "connection_strength": "Сила",
                  "influence_on_risk": "Влияние на оценку", "limitation": "Ограничение"}
        parts = [f"{label}: {sanitize_text(value.get(key))}" for key, label in labels.items() if sanitize_text(value.get(key))]
        return "\n".join(parts)
    return sanitize_text(value)


def finalize_operation(llm_op: dict, materials: Dict[str, str], has_history: bool, profile: DataProfile) -> dict:
    """Ответ анализатора -> поля итоговой выписки. Уровень риска не меняется; маршрут согласуется с ним."""
    risk = to_risk_level(llm_op.get("risk_level"))
    factors = [f for f in normalize_to_list(llm_op.get("risk_factors")) if f in RISK_FACTORS]
    route = llm_op.get("check_route") if llm_op.get("check_route") in ROUTES else None
    if risk == 0:
        factors = []
        if route not in LOW_RISK_ROUTES:
            route = "documents" if normalize_to_list(llm_op.get("documents_to_request")) else "none"
    elif risk == 1 and route in (None, *LOW_RISK_ROUTES):
        route = next((r for f, r in _ROUTE_BY_FACTOR if f in factors), "counter_performance")
    route = route or "none"

    route_label, article = ROUTES[route]
    documents = str_list(llm_op.get("documents_to_request"))
    matched = str_list(llm_op.get("criteria_matched"))
    missing = str_list(llm_op.get("criteria_missing"))
    if risk == 1 and not missing and route in ROUTE_CHECKS:
        missing = list(ROUTE_CHECKS[route]["questions"])
    readiness = sanitize_text(llm_op.get("challenge_readiness"))
    criteria_parts = [("Подтверждено", matched), ("Установить", missing)]
    challenge_criteria = "\n".join(f"{label}: " + "; ".join(items) for label, items in criteria_parts if items)
    if readiness:
        challenge_criteria += ("\n" if challenge_criteria else "") + f"Готовность к оценке: {readiness}"

    goal = sanitize_text(llm_op.get("verification_goal"))
    change = sanitize_text(llm_op.get("risk_change_conditions"))
    if route == "none":
        recommendation = "Дополнительные действия не требуются."
    else:
        recommendation = ("Приоритетная ручная проверка. " if risk == 1 else "") + route_label + "."
    if goal and goal.lower() != "не требуется":
        recommendation += f" Цель проверки: {goal}."
    if documents:
        recommendation += " Запросить: " + "; ".join(documents) + "."
    if change:
        recommendation += " Оценка изменится, если: " + change[0].lower() + change[1:]

    legal_basis = _resolve_refs(llm_op.get("legal_refs"), materials, "application", "Применение к операции")
    court_basis = _resolve_refs(llm_op.get("practice_refs"), materials, "relevance", "Значение для проверки")
    used_tools = (["search_practice_and_normative"] if legal_basis or court_basis else []) + \
                 (["get_conterparty_risk"] if has_history else [])
    return {
        "risk_level": risk,
        "risk_factors": [RISK_FACTORS[f] for f in factors],
        "check_route": route,
        "legal_qualification": route_label + (f" ({article})" if risk == 1 and article else ""),
        "legal_basis": legal_basis,
        "court_basis": court_basis,
        "counterparty_category": sanitize_text(llm_op.get("counterparty_category")) or "не установлено по доступным данным",
        "decision_argumentation": sanitize_text(llm_op.get("decision_argumentation")),
        "risk_explanation": sanitize_text(llm_op.get("risk_explanation")),
        "challenge_criteria": challenge_criteria,
        "recommendation": recommendation,
        "connections_basis": _connections_text(llm_op.get("connections") or llm_op.get("connections_note"), profile),
        "documents_to_request": documents,
        "verification_goal": goal if goal.lower() != "не требуется" else "",
        "used_tools": used_tools,
    }


# ───────────────────────── Вызов LLM с контролем состава ответа ─────────────────────────

def invoke_with_operations_guard(rt: Runtime, model: Any, messages: list, expected_ids: List[str], role: str) -> List[dict]:
    """
    Вызов LLM с контролем состава ответа: при обрыве — один повтор вызова, при битой структуре — ремонт JSON
    через llm_helper. Возвращает только операции с ожидаемыми id.
    """
    expected = set(expected_ids)

    def _parse(raw: Any) -> tuple[List[dict], set]:
        ops = [op for op in extract_operations_list(extract_json(raw)) if str(op.get("id")) in expected]
        return ops, expected - {str(op.get("id")) for op in ops}

    def _invoke() -> Any:
        try:
            return invoke_llm(model, messages, role=role)
        except Exception as exc:  # noqa: BLE001
            logger.error("%s: ошибка вызова LLM: %s", role, exc)
            return None

    raw = _invoke()
    ops, missing = _parse(raw)
    if ops and not missing:
        return ops

    raw_text = message_text(getattr(raw, "content", raw)) if raw is not None else ""
    if _truncated(raw_text):
        logger.warning("%s: ответ оборван, повторный вызов.", role)
        raw2 = _invoke()
        ops2, missing2 = _parse(raw2)
        if len(ops2) >= len(ops):
            ops, missing = ops2, missing2
            raw_text = message_text(getattr(raw2, "content", raw2)) if raw2 is not None else raw_text

    if not ops and raw_text.strip():
        for _ in range(2):
            try:
                repaired = invoke_llm(rt.helper, make_messages(
                    JSON_REPAIR_SYSTEM_PROMPT, f"Ожидаемые id: {sorted(expected)}\n\n{raw_text[:16000]}"), role="json-repair")
            except Exception as exc:  # noqa: BLE001
                logger.error("%s: ремонт JSON не удался: %s", role, exc)
                break
            ops, missing = _parse(repaired)
            if ops:
                logger.info("%s: JSON восстановлен, операций: %d", role, len(ops))
                break

    if missing:
        logger.warning("%s: нет ответа по %d из %d операций.", role, len(missing), len(expected))
    return ops


def _truncated(text: str) -> bool:
    return looks_truncated(text)


# ───────────────────────── Узлы графа ─────────────────────────

def _tool_request_key(name: Any, args: Any) -> str:
    return f"{name}|{json.dumps(args, ensure_ascii=False, sort_keys=True, default=str)}"


def _collect_requested_tools(raw_response: Any, orchestrator_result: dict) -> List[dict]:
    """Нативные tool_calls и JSON-поле requested_tools, без дублей."""
    requested, seen = [], set()
    json_requests = orchestrator_result.get("requested_tools") or []
    native = [{"name": c.get("name"), "args": c.get("args") or {}} for c in getattr(raw_response, "tool_calls", None) or []]
    for req in native + [r for r in json_requests if isinstance(r, dict)]:
        name = req.get("name") or req.get("tool_name") or req.get("tool")
        args = req.get("args") or req.get("arguments") or req.get("tool_args") or {}
        key = _tool_request_key(name, args)
        if name and key not in seen:
            seen.add(key)
            requested.append({"name": name, "args": args})
    return requested


def _collect_materials(tool_results: List[dict]) -> tuple[Dict[str, str], Dict[str, dict]]:
    """Нумерованные нормы (N…) и позиции судов (P…) + история контрагентов по псевдониму."""
    materials, histories = {}, {}
    for record in tool_results:
        if not record["success"]:
            continue
        result = record["result"] or {}
        if record["name"] == "search_practice_and_normative":
            for item in result.get("normative", []):
                text = f"{str(item.get('article', '')).strip()}: {str(item.get('content', '')).strip()}".strip(": ")
                if text:
                    materials[f"N{sum(k.startswith('N') for k in materials) + 1}"] = text
            for item in result.get("court_practice", []):
                text = " ".join(filter(None, [str(item.get("court_position") or "").strip(),
                                              str(item.get("key_details") or "").strip()]))
                if text:
                    materials[f"P{sum(k.startswith('P') for k in materials) + 1}"] = text
        elif record["name"] == "get_conterparty_risk":
            histories[str(record["args"].get("inn"))] = {k: v for k, v in result.items() if k != "counterparty"}
    return materials, histories


class BatchAnalysis:
    """Один пакет операций: оркестратор -> инструменты -> оркестратор -> анализатор."""

    def __init__(self, rt: Runtime, profile: DataProfile):
        self.rt = rt
        self.profile = profile
        self.orchestrator_prompt, self.analyzer_prompt = build_system_prompts(
            profile.has_affiliation, profile.has_business_profile, profile.has_anomaly_scores)

    def _orchestrator(self, state: dict) -> None:
        remaining = max(state["remaining_steps"] - 1, 0)
        tool_results = [{"name": r["name"], "result": r["result"]} for r in state["tool_results"] if r["success"]]
        human = (
            f"Операции ({len(state['operations'])}):\n{dumps(state['operations'])}\n\n"
            f"Результаты инструментов:\n{dumps(tool_results) if tool_results else 'нет'}\n\n"
            + ("Инструменты больше вызывать нельзя. " if remaining == 0 else "")
            + "Верни JSON."
        )
        model = self.rt.orchestrator_llm() if remaining > 0 else self.rt.llm
        messages = make_messages(self.orchestrator_prompt, human)
        try:
            raw = invoke_llm(model, messages, role="orchestrator")
        except Exception:  # noqa: BLE001
            if model is self.rt.llm:
                raise
            # сервер не принял схему инструментов: дальше работаем без нативных вызовов (инструменты — через JSON)
            logger.warning("Оркестратор: вызов с инструментами не удался, переход на обычный режим без нативных tool_calls.")
            self.rt._orchestrator_llm = self.rt.llm
            raw = invoke_llm(self.rt.llm, messages, role="orchestrator")
        parsed = extract_json(raw)
        parsed = parsed if isinstance(parsed, dict) and not parsed.get("_parse_error") else {}
        # При нативном вызове инструмента content пуст — сохраняем предыдущую карту фактов.
        state["orchestrator_result"] = parsed if parsed.get("operations") else state["orchestrator_result"]
        state["remaining_steps"] = remaining
        state["tool_to_check"] = _collect_requested_tools(raw, parsed) if remaining > 0 else []

    def _tools(self, state: dict) -> None:
        reverse_map = state["inn_reverse_map"]
        real_to_alias = {real: alias for alias, real in reverse_map.items()}
        results = state["tool_results"]
        executed = {_tool_request_key(r["name"], r["args"]) for r in results}
        for request in state["tool_to_check"]:
            name, args = request["name"], dict(request["args"])
            key = _tool_request_key(name, args)
            if key in executed:
                continue
            executed.add(key)
            record = {"name": name, "args": args, "success": False, "result": None}
            if name not in TOOL_NAMES:
                logger.error("Запрошен неизвестный инструмент: %s", name)
            else:
                exec_args = dict(args)
                if name == "get_conterparty_risk":
                    exec_args["inn"] = reverse_map.get(str(args.get("inn", "")).strip(), "")
                try:
                    result = run_tool(self.rt, name, exec_args)
                    if name == "get_conterparty_risk":
                        result = {**result, "counterparty": args.get("inn")}
                    result = json.loads(result) if isinstance(result, str) else result
                    text = dumps(result)
                    for real, alias in real_to_alias.items():
                        text = text.replace(real, alias)
                    record.update(success=True, result=json.loads(text))
                except Exception:  # noqa: BLE001
                    logger.exception("Ошибка инструмента %s", name)
            results.append(record)
        state["tool_to_check"] = []

    def _analyzer(self, state: dict) -> None:
        materials, histories = _collect_materials(state["tool_results"])
        first_stage = {str(op.get("id")): op for op in extract_operations_list(state["orchestrator_result"] or {})}
        had_history = {str(op["id"]) for op in state["operations"] if op["counterparty_id"] in histories}

        ops_for_analyzer = []
        for op in state["operations"]:
            item = {k: v for k, v in op.items() if k != "counterparty_id"}
            stage = first_stage.get(op["id"])
            if stage:
                item["first_stage"] = {k: v for k, v in stage.items() if k != "id"}
            if op["counterparty_id"] in histories:
                item["counterparty_history"] = histories[op["counterparty_id"]]
            ops_for_analyzer.append(item)

        materials_text = "\n".join(f"{k}: {v}" for k, v in materials.items()) or "нет"
        human = (f"Операции ({len(ops_for_analyzer)}):\n{dumps(ops_for_analyzer)}\n\n"
                 f"legal_materials:\n{materials_text}\n\nВерни JSON.")
        llm_ops = invoke_with_operations_guard(
            self.rt, self.rt.llm, make_messages(self.analyzer_prompt, human),
            [op["id"] for op in state["operations"]], role="analyzer")
        finalized = []
        for llm_op in llm_ops:
            result = finalize_operation(llm_op, materials, str(llm_op["id"]) in had_history, self.profile)
            if result["risk_level"] is not None:
                finalized.append({"id": str(llm_op["id"]), **result})
        state["analyzer_ops"] = finalized

    def run(self, batch_label: Any, records: List[dict]) -> dict:
        """Пакет операций -> {idx: результат}. Сопоставление id -> idx делает код, а не модель."""
        id_to_idx, aliases, llm_ops = {}, {}, []
        for position, record in enumerate(records, start=1):
            local_id = str(position)
            id_to_idx[local_id] = str(record["idx"])
            inn = resolve_counterparty_inn(record)
            if inn and inn not in aliases:
                aliases[inn] = f"ORG_{len(aliases) + 1}"
            llm_ops.append(to_llm_operation(record, local_id, aliases.get(inn), self.profile))

        state: dict = {
            "operations": llm_ops, "remaining_steps": self.rt.cfg.orchestrator_max_steps, "tool_to_check": [],
            "tool_results": [], "orchestrator_result": {}, "analyzer_ops": [],
            "inn_reverse_map": {alias: inn for inn, alias in aliases.items()},
        }
        try:
            self._orchestrator(state)
            while state["tool_to_check"] and state["remaining_steps"] > 0:
                self._tools(state)
                self._orchestrator(state)
            self._analyzer(state)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Пакет %s: ошибка графа", batch_label)
            return {"batch": batch_label, "results": {}, "status": f"error: {exc}"}

        results = {}
        for op in state["analyzer_ops"]:
            idx = id_to_idx.get(op.pop("id"))
            if idx and idx not in results:
                results[idx] = op
        logger.info("Пакет %s: результатов %d из %d", batch_label, len(results), len(records))
        return {"batch": batch_label, "results": results, "status": "success" if results else "empty"}
