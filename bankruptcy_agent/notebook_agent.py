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
from .progress_utils import ProgressReporter, friendly_tool_message
from .prompts import ANALYZER_SYSTEM_PROMPT, ORCHSTRATOR_SYSTEM_PROMPT
from .utils import clean_text as clean_text_or_empty

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


def _content_text(message: Any) -> str:
    if message is None:
        return ""
    if isinstance(message, str):
        return message
    if isinstance(message, dict):
        return json.dumps(message, ensure_ascii=False, default=str)
    return str(getattr(message, "content", message))


def _looks_truncated(text: str) -> bool:
    """True, если ответ похож на ОБРЫВ: JSON начался, но скобки не закрылись
    (часть операций не сгенерирована), либо ответ пуст. Полный, но синтаксически
    битый JSON (все скобки закрыты) обрывом не считается -- такой чинит llm_helper."""
    t = (text or "").strip()
    if t.startswith("```"):
        t = t.strip("`").strip()
    if not t:
        return True
    start = next((i for i, ch in enumerate(t) if ch in "{["), None)
    if start is None:
        return False
    depth = 0
    in_str = False
    esc = False
    for ch in t[start:]:
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
    return depth > 0


JSON_REPAIR_MAX_CHARS = 16000

JSON_REPAIR_SYSTEM_PROMPT = (
    "Ты — модуль восстановления структуры JSON. На вход даётся текст, который ДОЛЖЕН быть "
    'JSON-объектом вида {"operations": [ ... ]}, но повреждён: лишний текст вокруг, markdown-'
    "ограждения, оборванный конец, висячие запятые, неэкранированные кавычки и т.п. "
    "Верни СТРОГО валидный JSON без markdown и без каких-либо пояснений, приведённый к схеме "
    '{"operations": [ <объекты операций> ]}. Сохрани СОДЕРЖИМОЕ операций как есть, включая поле '
    "idx у каждой операции. Ничего не придумывай и не добавляй новых операций; если часть текста "
    "оборвана, восстанови структуру только уже присутствующих операций. Ответ — только JSON."
)


def _repair_json_with_llm_helper(runtime: AgentRuntime, broken_text: str, expected_idx: list[Any]) -> dict[str, Any]:
    """Чинит структуру уже полученного ответа анализатора дешёвым llm_helper --
    сам анализ не перезапускается, чинится только JSON. Возвращает {} при неудаче."""
    if runtime.llm_helper is None:
        logger.error("Восстановление JSON: llm_helper не настроен в bankruptcy_agent/models.py")
        return {}
    idx_list = [str(i) for i in expected_idx if i is not None]
    human = (
        f"Ожидаются операции с такими idx ({len(idx_list)} шт.): {idx_list}.\n"
        "Ниже повреждённый ответ, который нужно привести к валидному JSON:\n\n"
        + (broken_text or "")[:JSON_REPAIR_MAX_CHARS]
    )
    try:
        parsed = _invoke_llm_json(runtime.llm_helper, system=JSON_REPAIR_SYSTEM_PROMPT, human=human)
    except Exception as exc:
        logger.error("Восстановление JSON: ошибка вызова llm_helper: %s", exc)
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _orchestrator_system_prompt() -> str:
    return ORCHSTRATOR_SYSTEM_PROMPT + """

ДОПОЛНИТЕЛЬНЫЕ ПРАВИЛА ВЫЗОВА TOOLS

Ты можешь вызывать tools только если они реально уменьшают неопределенность.

Если нужен нормативный или судебный поиск, вызывай search_practice_and_normative.
Передавай в него:
- search_task: словесную постановку ПРАВОВОЙ задачи (ситуация, гипотеза, каких
  фактов не хватает) развёрнутым текстом;
- hypotheses: до двух проверяемых гипотез;
- operation_context: структурированный КОНТЕКСТ операций (факты и признаки).
Не передавай в этот tool тип операции / operation_type / название категории как
отдельный аргумент или как весь search_task — тип не является поисковым ключом,
поиск строится по фактам и правовому вопросу. Если тип важен, он уже есть в
operation_context.transaction_category.
search_practice_and_normative можно вызвать максимум 2 раза на один кластер.

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
- OrchestratorClusterResult — всю информацию, которую оркестратор смог собрать по кластеру
- tool_results — результаты вызовов инструментов по кластеру
- operations — сами операции кластера

Ты должен:
1. Проанализировать КАЖДУЮ операцию кластера.
2. Использовать всю информацию от оркестратора как контекст для каждой операции.
3. Для каждой операции определить risk_level, legal_qualification, recommendation.
4. Сформулировать общую оценку кластера (overall_risk_assessment).
5. Вернуть результат по ВСЕМУ кластеру с разбивкой по операциям.

Ты НЕ вызываешь tools.
Ты НЕ строишь новые гипотезы — ты используешь гипотезы оркестратора.
Ты принимаешь решение на основе того, что передал оркестратор.

В текстовых значениях итогового JSON запрещены английские слова; ключи JSON остаются
на английском.

Формат ответа — обертка кластера, где каждый элемент operations строго соответствует
объекту операции из раздела «ФОРМАТ ОТВЕТА» выше:
{
  "cluster_summary": "",
  "operations": [ ...объекты операций в формате из раздела «ФОРМАТ ОТВЕТА»... ],
  "overall_risk_assessment": "",
  "used_tools": []
}

Количество объектов в operations должно строго совпадать с количеством операций в кластере.
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


def _extract_operations(result: dict[str, Any]) -> list[dict[str, Any]]:
    ops = result.get("operations", [])
    return [op for op in ops if isinstance(op, dict)] if isinstance(ops, list) else []


def call_analyzer_with_guard(
    runtime: AgentRuntime,
    *,
    operations: list[dict[str, Any]],
    orchestrator_result: dict[str, Any],
    tool_results: list[dict[str, Any]],
    warnings: list[str],
    tool_limit_reached: bool,
    unexecuted_requested_tools: list[dict[str, Any]],
) -> dict[str, Any]:
    """Вызывает анализатора и контролирует структуру ответа (см. cell 23 тетрадки
    agent_v3_adaptive_clusters_1.ipynb, _invoke_with_operations_guard):

    - ОБРЫВ ответа (часть операций не сгенерирована) -> ОДИН повторный вызов
      анализатора, потому что потерянный контент llm_helper восстановить не может;
    - битая только структура (контент на месте, но JSON невалиден) -> ремонт дешёвым
      llm_helper, без повтора анализатора (дороже перезапускать весь кластер).
    """
    expected_set = {str(op.get("idx")) for op in operations if op.get("idx") is not None}
    kwargs = dict(
        operations=operations, orchestrator_result=orchestrator_result, tool_results=tool_results,
        warnings=warnings, tool_limit_reached=tool_limit_reached,
        unexecuted_requested_tools=unexecuted_requested_tools,
    )

    result = call_analyzer(runtime, **kwargs)
    ops = _extract_operations(result)
    missing = expected_set - {str(op.get("idx")) for op in ops if op.get("idx") is not None}
    if ops and not missing:
        return result

    raw_text = str(result.get("_raw_response") or "")

    if _looks_truncated(raw_text):
        logger.warning("Анализатор: ответ оборван, повторный вызов (1 раз), кластер операций=%d", len(operations))
        result2 = call_analyzer(runtime, **kwargs)
        ops2 = _extract_operations(result2)
        if len(ops2) >= len(ops):
            result, ops = result2, ops2
            missing = expected_set - {str(op.get("idx")) for op in ops if op.get("idx") is not None}
        if ops and not missing:
            return result

    if not ops:
        logger.warning("Анализатор: разобрано 0 операций, восстановление структуры через llm_helper")
        repaired = _repair_json_with_llm_helper(runtime, raw_text, [op.get("idx") for op in operations])
        repaired_ops = _extract_operations(repaired)
        if repaired_ops:
            repaired.setdefault("operations", repaired_ops)
            logger.info("Анализатор: структура JSON восстановлена llm_helper, операций: %d", len(repaired_ops))
            return repaired

    if missing and ops:
        logger.warning("Анализатор: разобрано %d операций из %d ожидаемых, не хватает idx=%s",
                       len(ops), len(expected_set), sorted(missing)[:10])
    return result


def run_cluster_agent(
    runtime: AgentRuntime,
    *,
    cluster_id: Any,
    cluster_df: pd.DataFrame,
    max_orchestrator_steps: int = 3,
    reporter: ProgressReporter | None = None,
) -> AgentClusterResult:
    operations = records_for_llm(cluster_df)
    n_ops = len(operations)
    tool_results: list[dict[str, Any]] = []
    warnings: list[str] = []
    orchestrator_result: dict[str, Any] = {}
    unexecuted_requested_tools: list[dict[str, Any]] = []

    remaining_steps = max_orchestrator_steps

    if reporter is not None:
        reporter.note(f"Кластер {cluster_id}: просматриваю {n_ops} операций и формирую гипотезы")

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

        if reporter is not None:
            reporter.note(f"Кластер {cluster_id}: информации недостаточно, обращаюсь к инструментам")
        for tool_req in requested_tools:
            if isinstance(tool_req, dict):
                if reporter is not None:
                    reporter.note(f"Кластер {cluster_id}: {friendly_tool_message(str(tool_req.get('name', '')))}")
                tool_results.append(execute_requested_tool(runtime, tool_req))
        remaining_steps = after

    if reporter is not None:
        reporter.note(f"Кластер {cluster_id}: формирую итоговую оценку риска по операциям")

    analyzer_result = call_analyzer_with_guard(
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
        # cluster_summary/overall_risk_assessment - поля уровня кластера (соседи с
        # operations в ответе анализатора), а не отдельной операции. Проставляем их
        # на каждую операцию, иначе они молча теряются при разборе и не доходят ни
        # до propagation.py (где уже перечислены в PROPAGATED_ANALYSIS_FIELDS), ни до UI.
        cluster_summary = clean_text_or_empty(analyzer_result.get("cluster_summary"))
        overall_risk_assessment = clean_text_or_empty(analyzer_result.get("overall_risk_assessment"))
        for op in parsed_operations:
            op.setdefault("cluster_id", cluster_id)
            op.setdefault("status", "success")
            op.setdefault("cluster_summary", cluster_summary)
            op.setdefault("overall_risk_assessment", overall_risk_assessment)
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
    reporter: ProgressReporter | None = None,
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

    # batch_group — результат адаптивной стратегии кластеров (clustering_strategy.py):
    # представители компактного кластера или чанк разреженного кластера. Если
    # колонки нет (старый вызов без адаптивной стратегии), группируем по cluster.
    group_key = "batch_group" if "batch_group" in transactions.columns else "cluster"

    cluster_results: list[AgentClusterResult] = []
    groups = list(transactions.groupby(group_key, sort=False))
    total_groups = max(len(groups), 1)
    for i, (cluster_id, cluster_df) in enumerate(tqdm(groups, desc="Батчи", unit="batch"), start=1):
        fraction = (i - 1) / total_groups
        if progress is not None:
            progress(fraction, desc=f"Агент анализирует кластер {cluster_id}")
        if reporter is not None:
            reporter.report(f"Анализирую кластер {i} из {len(groups)} ({len(cluster_df)} операций)", fraction)
        logger.info("Обработка кластера %s: операций=%d", cluster_id, len(cluster_df))
        try:
            cluster_results.append(
                run_cluster_agent(
                    runtime,
                    cluster_id=cluster_id,
                    cluster_df=cluster_df,
                    max_orchestrator_steps=max_orchestrator_steps,
                    reporter=reporter,
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
    if reporter is not None:
        reporter.report(f"Кластерный анализ завершен: обработано {len(groups)} кластеров", 1.0)
    return result_df
