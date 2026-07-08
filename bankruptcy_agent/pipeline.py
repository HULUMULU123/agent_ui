from __future__ import annotations

import logging
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
from tqdm.auto import tqdm

from .config import COURT_FILING_DATE_DEFAULT, MAX_OPERATIONS_PER_LLM_BATCH, SPARK_ENRICHMENT_PATH
from .dashboard import build_payload_from_agent_outputs
from .fallback import deterministic_operation_analysis
from .io import load_counterparty_enrichment, read_statement_table
from .models import risk_db as default_risk_db
from .output_normalizer import normalize_agent_output
from .preprocessing import prepare_transactions
from .progress_utils import ProgressReporter, StatusCallback
from .propagation import propagate_cluster_analysis, run_second_pass
from .real_agent_bridge import try_run_real_agent
from .risk_memory import update_risk_memory_from_results
from .schemas import AgentRunArtifacts

logger = logging.getLogger("bankruptcy_agent.pipeline")

ROOT = Path(__file__).resolve().parent.parent
EXPORTS_DIR = ROOT / "assets" / "dashboard_exports"


def _make_analyze_fn(use_real_agent: bool, reporter: ProgressReporter | None = None):
    """Единый путь анализа батча: реальный агент, иначе детерминированный fallback.

    Используется и для анализа представителей (первый проход), и для второго
    прохода по операциям с низкой уверенностью — так пропагация одинаково
    надежна независимо от того, настроен ли реальный LLM. `reporter` привязан
    к диапазону процентов конкретной фазы (первый/второй проход), поэтому
    сообщения внутри анализа батча получают корректный процент в UI.
    """
    def analyze(batch_df: pd.DataFrame) -> pd.DataFrame:
        if batch_df.empty:
            return pd.DataFrame()
        result = try_run_real_agent(batch_df, reporter=reporter) if use_real_agent else None
        if result is None or result.empty:
            result = deterministic_operation_analysis(batch_df, reporter=reporter)
        return result

    return analyze


def run_agent_pipeline(
    file_path: str | Path,
    *,
    progress: Any | None = None,
    use_real_agent: bool = True,
    court_filing_date: str = COURT_FILING_DATE_DEFAULT,
    status_callback: StatusCallback | None = None,
) -> AgentRunArtifacts:
    """Единый entrypoint для Gradio: файл -> agent output -> dashboard payload.

    Порядок шагов (перенесен из agent_v3_adaptive_clusters_1.ipynb):
    подготовка + адаптивная выборка -> анализ представителей -> распространение
    на всю выписку -> второй проход по низкой уверенности -> обновление памяти
    риска -> нормализация -> payload -> экспорт таблиц в ZIP.

    `status_callback(message, percent)` — дружелюбные сообщения о ходе анализа
    для UI сотрудника (не путать с `progress`, который управляет встроенным
    прогресс-баром Gradio). Проценты между этапами распределены неравномерно:
    анализ представителей и второй проход — самые долгие этапы (реальные вызовы
    LLM по кластерам или построчный разбор в fallback-режиме), поэтому им
    отдана большая часть шкалы.
    """
    steps = [
        "Чтение файла",
        "Подготовка колонок и адаптивной LLM-выборки",
        "Анализ представителей кластеров",
        "Распространение результатов на всю выписку",
        "Повторный анализ операций с низкой уверенностью",
        "Обновление памяти риска контрагентов",
        "Сбор таблиц, графиков и KPI",
        "Экспорт таблиц пайплайна в ZIP",
    ]
    step_bounds = {
        "Чтение файла": (0, 3),
        "Подготовка колонок и адаптивной LLM-выборки": (3, 8),
        "Анализ представителей кластеров": (8, 55),
        "Распространение результатов на всю выписку": (55, 62),
        "Повторный анализ операций с низкой уверенностью": (62, 85),
        "Обновление памяти риска контрагентов": (85, 90),
        "Сбор таблиц, графиков и KPI": (90, 96),
        "Экспорт таблиц пайплайна в ZIP": (96, 100),
    }
    step_messages = {
        "Чтение файла": "Читаю загруженный файл",
        "Подготовка колонок и адаптивной LLM-выборки": "Готовлю данные: очищаю колонки и разбиваю операции на кластеры по схожести",
        "Распространение результатов на всю выписку": "Переношу выводы представителей кластеров на похожие операции выписки",
        "Обновление памяти риска контрагентов": "Обновляю память риска по контрагентам",
        "Сбор таблиц, графиков и KPI": "Формирую таблицы, графики и итоговое заключение",
        "Экспорт таблиц пайплайна в ZIP": "Собираю архив таблиц анализа",
    }
    reporter = ProgressReporter(status_callback)

    path = Path(file_path)
    df: pd.DataFrame | None = None
    enrichment_df: pd.DataFrame | None = None
    prepared: pd.DataFrame | None = None
    df_unique: pd.DataFrame | None = None
    cluster_strategy: dict[Any, dict[str, Any]] = {}
    diagnostics_df: pd.DataFrame | None = None
    analyzed_representatives: pd.DataFrame | None = None
    full_analysis: pd.DataFrame | None = None
    analysis: pd.DataFrame | None = None
    warnings: list[str] = []
    second_pass_updated = 0

    for i, step in enumerate(tqdm(steps, desc="Анализ агентом", unit="step"), start=1):
        if progress is not None:
            progress((i - 1) / len(steps), desc=step)
        start_pct, end_pct = step_bounds[step]
        if step in step_messages:
            reporter.report(step_messages[step], start_pct / 100)

        if step == "Чтение файла":
            df = read_statement_table(path)
            print(f"[agent] Прочитан файл: {path.name}; строк={len(df)}, колонок={len(df.columns)}", flush=True)

            enrichment_df = load_counterparty_enrichment(SPARK_ENRICHMENT_PATH)
            if enrichment_df.empty:
                print("[agent] Обогащение по ИНН пропущено: SPARK_ENRICHMENT_PATH не задан или файл не найден.", flush=True)
                reporter.note("Таблица обогащения контрагентов недоступна — анализ продолжается без неё")
            else:
                print(f"[agent] Обогащение контрагентов загружено: {len(enrichment_df)} строк, {len(enrichment_df.columns)} колонок", flush=True)
                reporter.note(f"Загружена таблица обогащения контрагентов по ИНН: {len(enrichment_df)} записей")

        elif step == "Подготовка колонок и адаптивной LLM-выборки":
            assert df is not None
            prepared, df_unique, cluster_strategy, diagnostics_df, warnings = prepare_transactions(
                df, court_filing_date=court_filing_date, enrichment_df=enrichment_df
            )
            print(f"[agent] Адаптивная выборка: {len(df_unique)} операций из {len(prepared)} в LLM ({len(cluster_strategy)} кластеров)", flush=True)
            reporter.report(f"Выборка готова: {len(df_unique)} операций из {len(prepared)} войдут в анализ ({len(cluster_strategy)} кластеров)", end_pct / 100)

        elif step == "Анализ представителей кластеров":
            assert df_unique is not None
            analyze_fn = _make_analyze_fn(use_real_agent, reporter=reporter.sub(start_pct, end_pct))
            analyzed_representatives = analyze_fn(df_unique)

        elif step == "Распространение результатов на всю выписку":
            assert prepared is not None and analyzed_representatives is not None
            full_analysis = propagate_cluster_analysis(prepared, analyzed_representatives, cluster_strategy)

        elif step == "Повторный анализ операций с низкой уверенностью":
            assert full_analysis is not None
            second_pass_ops_count = int(full_analysis.get("needs_review", pd.Series(dtype=bool)).astype(bool).sum()) if "needs_review" in full_analysis.columns else 0
            if second_pass_ops_count:
                reporter.report(f"Повторно анализирую {second_pass_ops_count} операций с низкой уверенностью", start_pct / 100)
            else:
                reporter.report("Операций с низкой уверенностью не найдено — повторный анализ не требуется", start_pct / 100)
            second_pass_analyze_fn = _make_analyze_fn(use_real_agent, reporter=reporter.sub(start_pct, end_pct))
            full_analysis, second_pass_updated = run_second_pass(
                full_analysis, second_pass_analyze_fn, max_operations_per_llm_batch=MAX_OPERATIONS_PER_LLM_BATCH
            )
            print(f"[agent] Второй проход: переанализировано операций: {second_pass_updated}", flush=True)

        elif step == "Обновление памяти риска контрагентов":
            assert full_analysis is not None and prepared is not None
            try:
                stats = update_risk_memory_from_results(default_risk_db, full_analysis, prepared, court_filing_date)
                print(f"[agent] Память риска обновлена: {stats}", flush=True)
            except Exception as exc:
                print(f"[agent] Не удалось обновить память риска: {type(exc).__name__}: {exc}", flush=True)

        elif step == "Сбор таблиц, графиков и KPI":
            assert full_analysis is not None and df_unique is not None
            analysis = normalize_agent_output(full_analysis, df_unique)

        elif step == "Экспорт таблиц пайплайна в ZIP":
            pass

    assert df is not None and prepared is not None and df_unique is not None and analysis is not None and full_analysis is not None

    export_tables = _build_export_tables(df, df_unique, diagnostics_df, analyzed_representatives, full_analysis)
    exports_zip_path = _write_export_zip(export_tables, path.stem)

    payload = build_payload_from_agent_outputs(
        df, prepared, df_unique, analysis, path.name, warnings,
        cluster_strategy=cluster_strategy,
        risk_db=default_risk_db,
        second_pass_updated=second_pass_updated,
    )
    payload.setdefault("meta", {})["exportsZipPath"] = str(exports_zip_path) if exports_zip_path else ""

    summary = payload.get("summary", {})
    if progress is not None:
        progress(1.0, desc="Payload сформирован")
    reporter.report("Анализ завершен, дашборд обновлен", 1.0)

    return AgentRunArtifacts(
        input_df=df,
        prepared_df=prepared,
        sampled_df=df_unique,
        analysis_df=analysis,
        payload=payload,
        summary=summary,
        cluster_strategy=cluster_strategy,
        export_tables=export_tables,
        exports_zip_path=str(exports_zip_path) if exports_zip_path else None,
    )


def _build_export_tables(
    input_df: pd.DataFrame,
    df_unique: pd.DataFrame,
    diagnostics_df: pd.DataFrame | None,
    analyzed_representatives: pd.DataFrame | None,
    full_analysis: pd.DataFrame,
) -> dict[str, pd.DataFrame]:
    """Таблицы-артефакты пайплайна, аналогичные экспортам тетрадки."""
    tables: dict[str, pd.DataFrame] = {
        "исходная_выписка": input_df,
        "уникальные_операции_для_llm": df_unique,
    }
    if diagnostics_df is not None and not diagnostics_df.empty:
        tables["диагностика_кластеров"] = diagnostics_df
    if analyzed_representatives is not None and not analyzed_representatives.empty:
        tables["анализ_представителей"] = analyzed_representatives
    tables["полная_выписка_с_анализом"] = full_analysis
    if "needs_review" in full_analysis.columns:
        review_queue = full_analysis[full_analysis["needs_review"] == True]  # noqa: E712
        if not review_queue.empty:
            tables["на_ручную_проверку"] = review_queue
    return tables


def _write_export_zip(export_tables: dict[str, pd.DataFrame], source_stem: str) -> Path | None:
    if not export_tables:
        return None
    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    zip_path = EXPORTS_DIR / f"agent_tables_{source_stem}_{timestamp}.zip"
    try:
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, table in export_tables.items():
                csv_bytes = table.to_csv(index=False).encode("utf-8-sig")
                archive.writestr(f"{name}.csv", csv_bytes)
        return zip_path
    except Exception as exc:
        logger.exception("Не удалось собрать ZIP-архив таблиц: %s", exc)
        return None
