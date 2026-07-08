from __future__ import annotations

import json
from typing import Any

from .agent_utils import content_from_message, extract_json_from_llm_response, normalize_to_list
from .config import MAX_QUERIES_PER_TYPE
from .models import AgentRuntime
from .prompts import LEGAL_MODULE_SYSTEM_PROMPT


def _make_messages(system: str, human: str) -> list[Any]:
    try:
        from langchain_core.messages import HumanMessage, SystemMessage
        return [SystemMessage(content=system), HumanMessage(content=human)]
    except Exception:
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": human},
        ]


def get_counterparty_risk_tool(*, runtime: AgentRuntime, inn: str, court_filing_date: str | None = None) -> dict[str, Any]:
    """Tool из тетрадки: получить исторический риск контрагента."""
    if not inn:
        return {"success": False, "error": "Не передан ИНН контрагента", "limitations": ["tool не выполнен"]}
    if runtime.risk_db is None:
        return {
            "success": False,
            "counterparty": inn,
            "error": "risk_db не настроен в bankruptcy_agent/models.py",
            "limitations": ["историческая память риска недоступна"],
        }
    try:
        # Риск динамический: считается по всем событиям контрагента на текущую
        # дату (recency-затухание идет от момента анализа, а не от даты дела).
        current = runtime.risk_db.compute_current_risk(inn=inn)
        events = runtime.risk_db.get_risk_events(inn=inn, court_filing_date=court_filing_date, limit=5)
        return {
            "success": True,
            "counterparty": inn,
            "historical_risk": current,
            "last_events": events,
            "usage_note": (
                "Использовать как дополнительный сигнал (H_COUNTERPARTY_RISK), "
                "а не как автоматическое основание risk_level текущей операции."
            ),
        }
    except Exception as exc:
        return {"success": False, "counterparty": inn, "error": f"{type(exc).__name__}: {exc}"}


def search_normative_base_tool(*, runtime: AgentRuntime, query: str, k: int = 2) -> Any:
    """Tool из тетрадки: поиск в нормативной базе через retriever.invoke(query)."""
    if runtime.retriever is None:
        return {
            "success": False,
            "error": "retriever не настроен в bankruptcy_agent/models.py",
            "limitations": ["нормативная база недоступна"],
        }
    try:
        docs = runtime.retriever.invoke(query)
        if not docs:
            return "Ничего не найдено в нормативной базе"
        chunks: list[str] = []
        for i, doc in enumerate(docs[:k], start=1):
            meta = getattr(doc, "metadata", {}) or {}
            page_content = getattr(doc, "page_content", str(doc))
            chunks.append("\n".join([
                f"[Фрагмент {i}]",
                f"Документ: {meta.get('law_name') or meta.get('source') or 'Неизвестный документ'}",
                f"Глава: {meta.get('chapter_title') or 'Неизвестная глава'}",
                f"Статья: {meta.get('article_title') or meta.get('article') or 'Неизвестная статья'}",
                f"Путь: {meta.get('path') or 'Нет пути до статьи'}",
                f"Текст: {str(page_content).strip()}",
            ]))
        return "\n---\n".join(chunks)
    except Exception as exc:
        return {"success": False, "error": f"Ошибка при поиске в нормативной базе: {type(exc).__name__}: {exc}"}


def search_case_law_practice_tool(*, runtime: AgentRuntime, statement: str, filters: dict[str, Any] | None = None, top_k: int = 1) -> Any:
    """Tool из тетрадки: поиск похожей судебной практики."""
    if runtime.practice_tool is None:
        return {
            "success": False,
            "error": "practice_tool не настроен в bankruptcy_agent/models.py",
            "limitations": ["поиск судебной практики недоступен"],
        }
    try:
        return runtime.practice_tool.search(statement=statement, filters=filters, top_k=top_k)
    except Exception as exc:
        return {"success": False, "error": f"Ошибка поиска судебной практики: {type(exc).__name__}: {exc}"}


def search_practice_and_normative_tool(
    *,
    runtime: AgentRuntime,
    search_task: str,
    hypotheses: list[Any] | None = None,
    operation_context: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Tool из тетрадки: LLM формирует запросы, затем ищутся нормативка и практика."""
    hypotheses = hypotheses or []
    operation_context = operation_context or []

    if runtime.llm_helper is None:
        return {
            "success": False,
            "error": "llm_helper не настроен в bankruptcy_agent/models.py",
            "case_law_practice": [],
            "normative_base": [],
            "limitations": ["не удалось сформировать поисковые запросы"],
        }

    context = {
        "search_task": search_task,
        "hypotheses": hypotheses,
        "operations_context": operation_context,
    }
    messages = _make_messages(LEGAL_MODULE_SYSTEM_PROMPT, json.dumps(context, ensure_ascii=False, indent=2, default=str))

    try:
        response = runtime.llm_helper.invoke(messages)
        response_dict = extract_json_from_llm_response(response)
    except Exception as exc:
        return {"success": False, "error": f"Ошибка llm_helper: {type(exc).__name__}: {exc}"}

    raw_law_queries = response_dict.get("case_law_queries", response_dict.get("case_law_practice", []))
    raw_norm_queries = response_dict.get("normative_queries", response_dict.get("normaive_queries", []))
    law_queries = [str(x) for x in normalize_to_list(raw_law_queries) if str(x).strip()][:MAX_QUERIES_PER_TYPE]
    norm_queries = [str(x) for x in normalize_to_list(raw_norm_queries) if str(x).strip()][:MAX_QUERIES_PER_TYPE]

    # Каждый тип поиска выполняется независимо, до MAX_QUERIES_PER_TYPE запросов
    # на тип: отсутствие/ошибка одного запроса не блокирует остальные.
    law_practice = []
    for query in law_queries:
        try:
            law_practice.append(search_case_law_practice_tool(runtime=runtime, statement=query))
        except Exception as exc:
            law_practice.append({"success": False, "error": f"{type(exc).__name__}: {exc}"})

    normative_base = []
    for query in norm_queries:
        try:
            normative_base.append(search_normative_base_tool(runtime=runtime, query=query))
        except Exception as exc:
            normative_base.append({"success": False, "error": f"{type(exc).__name__}: {exc}"})

    return {
        "success": True,
        "generated_queries": {"case_law_queries": law_queries, "normative_queries": norm_queries},
        "case_law_practice": law_practice,
        "normative_base": normative_base,
    }


def execute_requested_tool(runtime: AgentRuntime, tool_request: dict[str, Any]) -> dict[str, Any]:
    """Выполняет один ToolRequest из orchestrator_result.requested_tools."""
    name = str(tool_request.get("name", "")).strip()
    args = tool_request.get("args") or {}
    if not isinstance(args, dict):
        args = {}
    tool_call_id = tool_request.get("tool_call_id") or name or "tool"
    try:
        if name == "get_conterparty_risk":
            result = get_counterparty_risk_tool(
                runtime=runtime,
                inn=str(args.get("inn") or args.get("counterparty") or args.get("counterparty_identifier") or ""),
                court_filing_date=args.get("court_filing_date"),
            )
        elif name == "search_practice_and_normative":
            result = search_practice_and_normative_tool(
                runtime=runtime,
                search_task=str(args.get("search_task") or args.get("query") or ""),
                hypotheses=args.get("hypotheses") or [],
                operation_context=args.get("operation_context") or args.get("operations_context") or [],
            )
        else:
            return {
                "tool_call_id": tool_call_id,
                "name": name or "unknown",
                "args": args,
                "success": False,
                "result": None,
                "error": f"Неизвестный tool: {name}",
                "limitations": ["tool не выполнен"],
            }
        return {
            "tool_call_id": tool_call_id,
            "name": name,
            "args": args,
            "success": bool(result.get("success", True)) if isinstance(result, dict) else True,
            "result": result,
            "error": result.get("error") if isinstance(result, dict) else None,
            "limitations": result.get("limitations", []) if isinstance(result, dict) else [],
        }
    except Exception as exc:
        return {
            "tool_call_id": tool_call_id,
            "name": name or "unknown",
            "args": args,
            "success": False,
            "result": None,
            "error": f"{type(exc).__name__}: {exc}",
            "limitations": ["tool завершился ошибкой"],
        }
