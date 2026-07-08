from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd


@dataclass(slots=True)
class AgentRunArtifacts:
    """Полный результат backend-пайплайна агента.

    input_df: исходная выписка как она прочитана из файла.
    prepared_df: полная выписка после приведения к контракту агента (все
        технические поля: interval_days, is_below_llm_threshold и т.д.).
    sampled_df: операции адаптивной LLM-выборки (представители/разреженные
        кластеры), переданные в LLM-анализ или fallback-анализ.
    analysis_df: нормализованный output анализатора по ВСЕЙ выписке (после
        распространения и второго прохода).
    payload: JSON-контракт для Gradio dashboard.
    summary: укороченная сводка из payload.
    cluster_strategy: диагностика адаптивной стратегии по кластерам.
    export_tables: именованные таблицы для ZIP-экспорта (см. build_export_zip).
    exports_zip_path: путь к собранному ZIP-архиву с таблицами пайплайна.
    """
    input_df: pd.DataFrame
    prepared_df: pd.DataFrame
    sampled_df: pd.DataFrame
    analysis_df: pd.DataFrame
    payload: dict[str, Any]
    summary: dict[str, Any]
    cluster_strategy: dict[Any, dict[str, Any]] | None = None
    export_tables: dict[str, pd.DataFrame] | None = None
    exports_zip_path: str | None = None
