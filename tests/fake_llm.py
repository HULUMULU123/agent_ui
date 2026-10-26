"""Детерминированная замена GigaChat для тестов: отвечает по протоколам промптов агента (JSON), без сети."""
from __future__ import annotations

import json
from types import SimpleNamespace

from finapp.agent.classifier import keyword_action, keyword_subject
from finapp.agent.catalog import CATALOG_ACTIONS


def _text(messages) -> tuple[str, str]:
    system = human = ""
    for m in messages:
        role = m[0] if isinstance(m, tuple) else getattr(m, "type", "")
        content = m[1] if isinstance(m, tuple) else m.content
        if role in ("system",):
            system = content
        else:
            human = content
    return system, human


def _ops(human: str) -> list[dict]:
    start = human.index("[")
    depth, end = 0, start
    for i in range(start, len(human)):
        depth += human[i] == "["
        depth -= human[i] == "]"
        if depth == 0:
            end = i
            break
    return json.loads(human[start:end + 1])


class FakeLLM:
    """Ведёт себя как GigaChat-3-Ultra для всех ролей. calls — счётчик по ролям; fail_roles — роли, возвращающие мусор."""

    def __init__(self, fail_roles: set[str] | None = None, tool_calls: bool = False):
        self.calls: dict[str, int] = {}
        self.fail_roles = fail_roles or set()
        self.tool_calls = tool_calls
        self.model = "GigaChat-3-Ultra"

    def bind_tools(self, tools):
        return self

    def _reply(self, role: str, payload) -> SimpleNamespace:
        self.calls[role] = self.calls.get(role, 0) + 1
        if role in self.fail_roles:
            return SimpleNamespace(content="не JSON вообще", tool_calls=[])
        return SimpleNamespace(content=json.dumps(payload, ensure_ascii=False), tool_calls=[])

    def invoke(self, messages):
        system, human = _text(messages)
        if "модуль классификации банковских операций" in system:
            out = []
            for op in json.loads(human)["operations"]:
                subject = keyword_subject(op["purpose"]) or "unknown"
                action = keyword_action(subject, op["purpose"], op["direction"]) if subject != "unknown" else None
                group = next((g for g, subs in __import__("finapp.agent.catalog", fromlist=["CATALOG"]).CATALOG["subjects"].items() if subject in subs), "other")
                if action not in CATALOG_ACTIONS.get(subject, []):
                    action = None
                out.append({"idx": op["idx"], "group": group, "subject_id": subject, "action_id": action,
                            "direction_consistent": True, "flags": [], "confidence": "high"})
            return self._reply("classifier", {"operations": out})
        if "не удалось уверенно отнести" in system:
            return self._reply("open_type", {"operations": [
                {"idx": op["idx"], "operation_type": "не установлено по доступным данным", "is_standard": False,
                 "requested_documents": []} for op in json.loads(human)["operations"]]})
        if "первый этап анализа" in system:
            ops = _ops(human)
            facts = [{"id": op["id"], "summary": op["purpose"][:80], "category": "не установлено", "confirmed_facts": [],
                      "unknown_facts": [], "ordinary_explanation": "обычная версия возможна", "risk_signals": [],
                      "protective_signals": [], "profile_signals": [], "affiliation_assessment": {},
                      "hypotheses": [], "documents_to_check": []} for op in ops]
            tools = []
            if "Результаты инструментов:\nнет" in human:
                alias = next((op["counterparty_id"] for op in ops if op["counterparty_id"].startswith("ORG_")), None)
                if alias:
                    tools.append({"name": "get_conterparty_risk", "args": {"inn": alias}})
                tools.append({"name": "search_practice_and_normative", "args": {"search_task": "Погашение долга до банкротства"}})
            reply = self._reply("orchestrator", {"operations": facts, "requested_tools": tools})
            return reply
        if "финальный этап" in system:
            ops = _ops(human)
            out = []
            for op in ops:
                debt = op["operation_type"] in ("Возврат займа", "Погашение кредита")
                risky = debt and op["direction"] == "убытие" and op["period"] in (
                    "менее 1 месяца до заявления", "1–6 месяцев до заявления")
                out.append({
                    "id": op["id"], "risk_level": 1 if risky else 0, "risk_factors": ["debt_repayment"] if risky else [],
                    "check_route": "preference" if risky else "none", "counterparty_category": "Займодавец / получатель возврата займа" if debt else "не установлено по доступным данным",
                    "criteria_matched": ["погашение ранее возникшего обязательства"] if risky else [],
                    "criteria_missing": ["были ли другие кредиторы"] if risky else [],
                    "challenge_readiness": "требуется приоритетная ручная проверка" if risky else "оснований для проверки по доступным данным не выявлено",
                    "decision_argumentation": "Платёж погашает долг в чувствительный период." if risky else "Операция обычная.",
                    "risk_explanation": "Решающий фактор — погашение долга." if risky else "Типовая операция.",
                    "verification_goal": "установить преимущество кредитора" if risky else "не требуется",
                    "documents_to_request": ["Договор займа", "Акт сверки"] if risky else [],
                    "risk_change_conditions": "Платёж шёл по графику.",
                    "legal_refs": [{"ref": "N1", "application": "Погашен долг до заявления."}] if risky else [],
                    "practice_refs": [], "connections": {"strongest_connection": "связи не передавались в этой выгрузке"}})
            return self._reply("analyzer", {"operations": out})
        if "Исправь структуру JSON" in system:
            return self._reply("repair", {"operations": []})
        if "формирования поисковых запросов" in system:
            return self._reply("legal_queries", {"normative_queries": ["предпочтительное удовлетворение кредитора"], "case_law_queries": ["Должник погасил долг"]})
        if "отбора и структурирования" in system:
            return self._reply("legal_select", {"normative": [{"article": "Статья о предпочтении", "content": "Погашение долга отдельному кредитору"}], "court_practice": []})
        return self._reply("unknown", {})
