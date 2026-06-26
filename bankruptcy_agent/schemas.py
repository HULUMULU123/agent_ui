from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd


@dataclass(slots=True)
class AgentRunArtifacts:
    """Полный результат backend-пайплайна агента.

    input_df: исходная выписка как она прочитана из файла.
    prepared_df: выписка после приведения к контракту агента.
    sampled_df: операции, переданные в LLM-анализ или fallback-анализ.
    analysis_df: нормализованный output анализатора.
    payload: JSON-контракт для Gradio dashboard.
    summary: укороченная сводка из payload.
    """
    input_df: pd.DataFrame
    prepared_df: pd.DataFrame
    sampled_df: pd.DataFrame
    analysis_df: pd.DataFrame
    payload: dict[str, Any]
    summary: dict[str, Any]
