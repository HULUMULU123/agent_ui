"""Реальный entrypoint агента для Gradio UI.

Этот файл теперь НЕ заглушка. Он запускает импортируемую версию агентного
pipeline из Jupyter-тетрадки:

    cluster -> orchestrator -> tools -> orchestrator -> analyzer

Перед запуском нужно заполнить модели и внешние зависимости в:

    bankruptcy_agent/models.py

Минимум:
    llm = ...

Рекомендуется:
    llm_helper = ...
    retriever = ...
    practice_tool = ...
    risk_db = ...
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from bankruptcy_agent.models import build_runtime
from bankruptcy_agent.notebook_agent import run_notebook_agent


def run_real_agent(transactions: pd.DataFrame) -> pd.DataFrame | list[dict[str, Any]] | dict[str, Any]:
    """Запуск агента из UI.

    `transactions` уже подготовлен слоем preprocessing.py: есть idx, cluster,
    interval, date, amount, purpose, anomaly_score, anomaly_model_score,
    debit/credit name/inn и другие доступные поля.

    Возвращается DataFrame в контракте AnalyzerClusterResult.operations.
    Дальше dashboard.py сам собирает таблицы, графики и KPI.
    """
    runtime = build_runtime()
    return run_notebook_agent(transactions, runtime=runtime, max_orchestrator_steps=3)
