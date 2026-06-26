from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
from tqdm.auto import tqdm

from .config import COURT_FILING_DATE_DEFAULT
from .dashboard import build_payload_from_agent_outputs
from .fallback import deterministic_operation_analysis
from .io import read_statement_table
from .output_normalizer import normalize_agent_output
from .preprocessing import prepare_transactions
from .real_agent_bridge import try_run_real_agent
from .schemas import AgentRunArtifacts
def run_agent_pipeline(
    file_path: str | Path,
    *,
    progress: Any | None = None,
    use_real_agent: bool = True,
    court_filing_date: str = COURT_FILING_DATE_DEFAULT,
) -> AgentRunArtifacts:
    """Единый entrypoint для Gradio: файл → agent output → dashboard payload."""
    steps = [
        "Чтение файла",
        "Подготовка колонок по контракту тетрадки",
        "Расчет временных признаков и LLM-выборки",
        "Запуск анализа операций",
        "Сбор таблиц, графиков и KPI",
        "Формирование dashboard_payload.json",
    ]
    path = Path(file_path)
    df: pd.DataFrame | None = None
    prepared: pd.DataFrame | None = None
    sampled: pd.DataFrame | None = None
    analysis: pd.DataFrame | None = None
    warnings: list[str] = []

    for i, step in enumerate(tqdm(steps, desc="Анализ агентом", unit="step"), start=1):
        if progress is not None:
            progress((i - 1) / len(steps), desc=step)
        if step == "Чтение файла":
            df = read_statement_table(path)
            print(f"[agent] Прочитан файл: {path.name}; строк={len(df)}, колонок={len(df.columns)}", flush=True)
        elif step == "Подготовка колонок по контракту тетрадки":
            assert df is not None
            prepared, sampled, warnings = prepare_transactions(df, court_filing_date=court_filing_date)
        elif step == "Запуск анализа операций":
            assert sampled is not None
            if use_real_agent:
                analysis = try_run_real_agent(sampled)
            if analysis is None:
                analysis = deterministic_operation_analysis(sampled)
            analysis = normalize_agent_output(analysis, sampled)
        elif step == "Сбор таблиц, графиков и KPI":
            pass

    assert df is not None and prepared is not None and sampled is not None and analysis is not None
    payload = build_payload_from_agent_outputs(df, prepared, sampled, analysis, path.name, warnings)
    summary = payload.get("summary", {})
    if progress is not None:
        progress(1.0, desc="Payload сформирован")
    return AgentRunArtifacts(input_df=df, prepared_df=prepared, sampled_df=sampled, analysis_df=analysis, payload=payload, summary=summary)
