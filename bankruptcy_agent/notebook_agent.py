from __future__ import annotations

import json
import logging
from typing import Any

import pandas as pd
from tqdm.auto import tqdm

from .agent_schemas import AgentClusterResult
from .agent_tools import execute_requested_tool
from .agent_utils import extract_json_from_llm_response, records_for_llm, to_jsonable
from .models import AgentRuntime
from .prompts import ANALYZER_SYSTEM_PROMPT, ORCHSTRATOR_SYSTEM_PROMPT

logger = logging.getLogger("bankruptcy_agent.runtime")


def _make_messages(system: str, human: str) -> list[Any]:
    try:
        from langchain_core.messages import HumanMessage, SystemMessage
        return [SystemMessage(content=system), HumanMessage(content=human)]
    except Exception:
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": human},
        ]


def _invoke_llm_json(llm: Any, *, system: str, human: str) -> dict[str, Any]:
    messages = _make_messages(system, human)
    response = llm.invoke(messages)
    return extract_json_from_llm_response(response)


def _orchestrator_system_prompt() -> str:
    return ORCHSTRATOR_SYSTEM_PROMPT + """

ДОПОЛНИТЕЛЬНЫЕ ПРАВИЛА ВЫЗОВА TOOLS

Ты можешь вызывать tools только если они реально уменьшают неопределенность.

Если нужен нормативный или судебный поиск, вызывай search_practice_and_normative.
В этот tool нужно передавать:
- search_task: словесную постановку задачи;
- hypotheses: проверяемые гипотезы;
- operation_context: структурированный контекст операции;
- tool_reason: зачем нужен tool.

Если нужна история риска контрагента, вызывай get_conterparty_risk.
Не вызывай get_conterparty_risk без валидного ИНН или наименования контрагента.

Если tools не нужны или все гипотезы уже проверены, не вызывай tools.
Тогда следующим узлом будет analyzer.

Не формируй финальный risk_level. Это делает analyzer.
"""


def _analyzer_system_prompt() -> str:
    return ANALYZER_SYSTEM_PROMPT + """

ДОПОЛНИТЕЛЬНЫЕ ПРАВИЛА

Ты — финальный анализатор. Ты работаешь с КЛАСТЕРОМ операций.

Ты получаешь от оркестратора:
- OrchestratorClusterResult — всю информацию, которую оркестратор смог собрать по кластеру;
- tool_results — результаты вызовов инструментов по кластеру;
- operations — сами операции кластера.

Ты должен:
1. Проанализировать КАЖДУЮ операцию кластера.
2. Использовать всю информацию от оркестратора как контекст для каждой операции.
3. Для каждой операции определить risk_level, legal_qualification, recommendation.
4. Сформулировать общую оценку кластера (overall_risk_assessment).
5. Вернуть результат по ВСЕМУ кластеру с разбивкой по операциям.

Ты НЕ вызываешь tools.
Ты НЕ строишь новые гипотезы — ты используешь гипотезы оркестратора.
Ты принимаешь решение на основе того, что передал оркестратор.

Формат ответа:
{
  "cluster_summary": "",
  "operations": [
    {
      "idx": "",
      "transaction_category": "",
      "amount": 0,
      "counterparty_category": "",
      "connections_basis": {
        "strongest_connection": "",
        "connection_set_summary": "",
        "connection_strength": "",
        "influence_on_risk": "",
        "limitation": ""
      },
      "challenge_criteria": {
        "potential_route": "",
        "criteria_matched": [],
        "criteria_missing": [],
        "documents_needed": [],
        "challenge_readiness": ""
      },
      "risk_level": 0,
      "legal_qualification": "",
      "legal_basis": [],
      "court_basis": [],
      "decision_argumentation": "",
      "risk_explanation": "",
      "recommendation": {
        "summary": "",
        "documents_to_request": [],
        "verification_goal": "",
        "risk_change_conditions": ""
      },
      "used_tools": []
    }
  ],
  "overall_risk_assessment": "",
  "used_tools": []
}

Количество объектов в operations должно строго совпадать с количеством операций в кластере.
Верни СТРОГО JSON без markdown.
"""


def call_orchestrator(
    runtime: AgentRuntime,
    *,
    operations: list[dict[str, Any]],
    tool_results: list[dict[str, Any]],
    remaining_steps_before: int,
    remaining_steps_after: int,
) -> dict[str, Any]:
    human = f"""
Текущее состояние анализа:

Количество операций в кластере: {len(operations)}

Операции кластера:
{json.dumps(operations, ensure_ascii=False, indent=2, default=str)}

Результаты предыдущих вызовов инструментов:
{json.dumps(tool_results, ensure_ascii=False, indent=2, default=str)}

Оставшиеся шаги:
- до вызова: {remaining_steps_before}
- после вызова: {remaining_steps_after}

Твоя задача:
1. Проанализировать ВСЕ операции кластера.
2. Собрать всю доступную информацию по кластеру.
3. Сформулировать гипотезы.
4. При необходимости вызвать tools.
5. Передать в анализатор полную информацию по кластеру.

Верни СТРОГО JSON без markdown.
"""
    return _invoke_llm_json(runtime.llm, system=_orchestrator_system_prompt(), human=human)


def call_analyzer(
    runtime: AgentRuntime,
    *,
    operations: list[dict[str, Any]],
    orchestrator_result: dict[str, Any],
    tool_results: list[dict[str, Any]],
    warnings: list[str],
    tool_limit_reached: bool,
    unexecuted_requested_tools: list[dict[str, Any]],
) -> dict[str, Any]:
    human = f"""
Сформируй финальный анализ по кластеру.

Количество операций: {len(operations)}

Операции кластера:
{json.dumps(operations, ensure_ascii=False, indent=2, default=str)}

Результат оркестратора (вся информация по кластеру):
{json.dumps(orchestrator_result, ensure_ascii=False, indent=2, default=str)}

Результаты инструментов:
{json.dumps(tool_results, ensure_ascii=False, indent=2, default=str)}

Предупреждения:
{json.dumps(warnings, ensure_ascii=False, indent=2, default=str)}

Достигнут лимит инструментов: {tool_limit_reached}
Невыполненные запрошенные инструменты:
{json.dumps(unexecuted_requested_tools, ensure_ascii=False, indent=2, default=str)}

Твоя задача:
1. Проанализировать КАЖДУЮ операцию кластера.
2. Использовать всю информацию от оркестратора как контекст.
3. Для каждой операции определить risk_level, legal_qualification, recommendation.
4. Сформулировать общую оценку кластера.
5. Вернуть результат по ВСЕМУ кластеру.

Верни СТРОГО JSON без markdown.
"""
    return _invoke_llm_json(runtime.llm, system=_analyzer_system_prompt(), human=human)


def run_cluster_agent(
    runtime: AgentRuntime,
    *,
    cluster_id: Any,
    cluster_df: pd.DataFrame,
    max_orchestrator_steps: int = 3,
) -> AgentClusterResult:
    operations = records_for_llm(cluster_df)
    n_ops = len(operations)
    tool_results: list[dict[str, Any]] = []
    warnings: list[str] = []
    orchestrator_result: dict[str, Any] = {}
    unexecuted_requested_tools: list[dict[str, Any]] = []

    remaining_steps = max_orchestrator_steps

    for _ in range(max_orchestrator_steps):
        before = remaining_steps
        after = max(remaining_steps - 1, 0)
        orchestrator_result = call_orchestrator(
            runtime,
            operations=operations,
            tool_results=tool_results,
            remaining_steps_before=before,
            remaining_steps_after=after,
        )
        requested_tools = orchestrator_result.get("requested_tools", [])
        if not isinstance(requested_tools, list):
            requested_tools = []

        for i, tool_req in enumerate(requested_tools):
            if isinstance(tool_req, dict) and not tool_req.get("tool_call_id"):
                tool_req["tool_call_id"] = f"{tool_req.get('name', 'tool')}_{i+1}"

        if after <= 0 and requested_tools:
            warnings.append("Лимит итераций оркестратора исчерпан: часть запрошенных tools не будет выполнена.")
            unexecuted_requested_tools = [r for r in requested_tools if isinstance(r, dict)]
            remaining_steps = after
            break

        if not requested_tools:
            remaining_steps = after
            break

        for tool_req in requested_tools:
            if isinstance(tool_req, dict):
                tool_results.append(execute_requested_tool(runtime, tool_req))
        remaining_steps = after

    analyzer_result = call_analyzer(
        runtime,
        operations=operations,
        orchestrator_result=orchestrator_result,
        tool_results=tool_results,
        warnings=warnings,
        tool_limit_reached=bool(unexecuted_requested_tools),
        unexecuted_requested_tools=unexecuted_requested_tools,
    )

    parsed_operations: list[dict[str, Any]] = []
    operations_list = analyzer_result.get("operations", [])
    if isinstance(operations_list, list):
        parsed_operations = [op for op in operations_list if isinstance(op, dict)]

    if parsed_operations:
        for op in parsed_operations:
            op.setdefault("cluster_id", cluster_id)
            op.setdefault("status", "success")
        return {
            "cluster_id": cluster_id,
            "parsed_analysis": parsed_operations,
            "raw_response": json.dumps(analyzer_result, ensure_ascii=False, default=str),
            "status": "success",
            "operations_count": len(parsed_operations),
        }

    return {
        "cluster_id": cluster_id,
        "parsed_analysis": None,
        "raw_response": json.dumps(analyzer_result, ensure_ascii=False, default=str),
        "status": "Не удалось извлечь results.operations из ответа анализатора",
        "operations_count": 0,
    }


def run_notebook_agent(
    transactions: pd.DataFrame,
    *,
    runtime: AgentRuntime,
    max_orchestrator_steps: int = 3,
    progress: Any | None = None,
) -> pd.DataFrame:
    """Запускает агентный цикл из тетрадки по кластерам.

    Это импортируемая версия секции `Запуск агентного анализа`:
    cluster -> orchestrator -> tools -> orchestrator -> analyzer -> flat DataFrame.
    """
    if transactions.empty:
        return pd.DataFrame()
    if "cluster" not in transactions.columns:
        transactions = transactions.copy()
        transactions["cluster"] = 0

    cluster_results: list[AgentClusterResult] = []
    groups = list(transactions.groupby("cluster", sort=False))
    for i, (cluster_id, cluster_df) in enumerate(tqdm(groups, desc="Кластеры", unit="cluster"), start=1):
        if progress is not None:
            progress((i - 1) / max(len(groups), 1), desc=f"Агент анализирует кластер {cluster_id}")
        logger.info("Обработка кластера %s: операций=%d", cluster_id, len(cluster_df))
        try:
            cluster_results.append(
                run_cluster_agent(
                    runtime,
                    cluster_id=cluster_id,
                    cluster_df=cluster_df,
                    max_orchestrator_steps=max_orchestrator_steps,
                )
            )
        except Exception as exc:
            logger.exception("Ошибка при обработке кластера %s", cluster_id)
            cluster_results.append({
                "cluster_id": cluster_id,
                "parsed_analysis": None,
                "raw_response": None,
                "status": f"Error: {type(exc).__name__}: {exc}",
                "operations_count": 0,
            })

    flat_data: list[dict[str, Any]] = []
    for item in cluster_results:
        cluster_id = item.get("cluster_id")
        status = item.get("status")
        parsed_analysis = item.get("parsed_analysis")
        if isinstance(parsed_analysis, list) and parsed_analysis:
            for tx in parsed_analysis:
                row = {str(k): to_jsonable(v) for k, v in tx.items()}
                row.setdefault("cluster_id", cluster_id)
                row.setdefault("status", status)
                flat_data.append(row)
        else:
            flat_data.append({
                "cluster_id": cluster_id,
                "status": status,
                "error": str(item.get("raw_response") or "Нет распознанных данных")[:500],
            })

    result_df = pd.DataFrame(flat_data)
    if progress is not None:
        progress(1.0, desc="Агентный анализ завершен")
    return result_df
