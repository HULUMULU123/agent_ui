from __future__ import annotations

import inspect
import os
from typing import Any

import pandas as pd

from .progress_utils import ProgressReporter


def try_run_real_agent(sampled_df: pd.DataFrame, reporter: ProgressReporter | None = None) -> pd.DataFrame | None:
    """Мост к настоящему агенту.

    Сейчас real_agent_entrypoint.py вызывает импортируемую версию agent pipeline
    из тетрадки. Если агент не настроен, по умолчанию UI использует fallback,
    чтобы демонстрация не падала. Для жесткого режима установите:

        STRICT_REAL_AGENT=1
    """
    try:
        from real_agent_entrypoint import run_real_agent  # type: ignore
    except Exception as exc:
        print(f"[agent] real_agent_entrypoint.py не найден или не импортируется: {exc}", flush=True)
        if reporter is not None:
            reporter.note("Реальный LLM-агент не настроен — использую детерминированный анализ")
        if os.getenv("STRICT_REAL_AGENT") == "1":
            raise
        return None

    try:
        if reporter is not None and _accepts_reporter(run_real_agent):
            result = run_real_agent(sampled_df.copy(), reporter=reporter)
        else:
            result = run_real_agent(sampled_df.copy())
        return normalize_raw_agent_return(result)
    except Exception as exc:
        print(f"[agent] Ошибка реального агента: {type(exc).__name__}: {exc}", flush=True)
        if reporter is not None:
            reporter.note("Реальный агент недоступен — использую детерминированный анализ")
        if os.getenv("STRICT_REAL_AGENT") == "1":
            raise
        print("[agent] Используется fallback-анализ в том же контракте.", flush=True)
        return None


def _accepts_reporter(callable_obj: Any) -> bool:
    try:
        signature = inspect.signature(callable_obj)
    except (TypeError, ValueError):
        return False
    return "reporter" in signature.parameters


def normalize_raw_agent_return(result: Any) -> pd.DataFrame | None:
    if result is None:
        return None
    if isinstance(result, pd.DataFrame):
        return result.copy()
    if isinstance(result, dict):
        if isinstance(result.get("operations"), list):
            return pd.DataFrame(result["operations"])
        if isinstance(result.get("analyzer_result"), dict) and isinstance(result["analyzer_result"].get("operations"), list):
            return pd.DataFrame(result["analyzer_result"]["operations"])
        if isinstance(result.get("final_json"), dict) and isinstance(result["final_json"].get("operations"), list):
            return pd.DataFrame(result["final_json"]["operations"])
        if isinstance(result.get("cluster_results"), list):
            return normalize_raw_agent_return(result["cluster_results"])
        return pd.DataFrame([result])
    if isinstance(result, list):
        flat: list[dict[str, Any]] = []
        for item in result:
            if isinstance(item, dict) and isinstance(item.get("parsed_analysis"), list):
                cluster_id = item.get("cluster_id")
                for op in item["parsed_analysis"]:
                    if isinstance(op, dict):
                        row = dict(op)
                        row.setdefault("cluster_id", cluster_id)
                        row.setdefault("status", item.get("status", "success"))
                        flat.append(row)
            elif isinstance(item, dict):
                flat.append(dict(item))
        return pd.DataFrame(flat)
    print("[agent] run_real_agent вернул неподдерживаемый тип; используется fallback.", flush=True)
    return None
