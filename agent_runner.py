from __future__ import annotations

import ast
import json
import time
from pathlib import Path
from typing import Any

import pandas as pd
from tqdm.auto import tqdm

from bankruptcy_agent import read_statement_table, run_agent_pipeline, save_payload
from bankruptcy_agent.config import (
    COURT_FILING_DATE_DEFAULT,
    FAST_MODE_DELAY_SECONDS,
    FAST_MODE_DIR,
    FAST_MODE_ENABLED,
)
from bankruptcy_agent.dashboard import build_payload_from_agent_outputs
from bankruptcy_agent.models import risk_db as default_risk_db
from bankruptcy_agent.preprocessing import prepare_transactions
from bankruptcy_agent.progress_utils import ProgressReporter, StatusCallback

ROOT = Path(__file__).resolve().parent
ASSETS = ROOT / "assets"
RUNTIME_PAYLOAD_PATH = ASSETS / "dashboard_payload_runtime.json"

# Заготовленный файл быстрого режима -- та же выписка, что и в реальном прогоне,
# но с уже готовыми колонками анализа агента (не только транзакционные поля).
FAST_MODE_STRUCTURED_COLUMNS = (
    "legal_basis", "court_basis", "challenge_criteria", "connections_basis",
    "recommendation", "requested_documents",
)
# Частые опечатки в названиях колонок -- поддерживаем оба варианта написания.
FAST_MODE_COLUMN_ALIASES = {
    "chllenge_criteria": "challenge_criteria",
    "challange_criteria": "challenge_criteria",
    "connection_basis": "connections_basis",
    "risk_exmplanation": "risk_explanation",
    "risk_explanation ": "risk_explanation",
    "recommenfation": "recommendation",
    "recomendation": "recommendation",
}

FAST_MODE_STEP_MESSAGES = [
    "Читаю подготовленный файл",
    "Готовлю колонки и разбиваю операции на кластеры по схожести",
    "Определяю типы операций",
    "Разбираю уже готовые результаты анализа по операциям",
    "Переношу выводы на похожие операции выписки",
    "Формирую таблицы, графики и итоговое заключение",
]


def _fast_mode_result_path(path: Path) -> Path | None:
    """Ищет подготовленный файл с тем же именем (без учёта регистра расширения)
    в FAST_MODE_DIR: .xlsx/.xls/.csv в этом порядке."""
    for suffix in (".xlsx", ".xls", ".csv"):
        candidate = FAST_MODE_DIR / f"{path.stem}{suffix}"
        if candidate.exists():
            return candidate
    return None


def _parse_structured_cell(value: Any) -> Any:
    """Разбирает ячейку Excel, которая может быть строкой, JSON (списком/словарём
    в двойных кавычках) или Python-репрезентацией списка/словаря (одинарные
    кавычки, как при прямом str() экспорте из pandas). Пустое значение -> None,
    чтобы normalize_agent_output сам подставил безопасный дефолт."""
    if isinstance(value, (dict, list)):
        return value
    if value is None:
        return None
    if isinstance(value, float) and pd.isna(value):
        return None
    text = str(value).strip()
    if not text:
        return None
    if not ((text.startswith("{") and text.endswith("}")) or (text.startswith("[") and text.endswith("]"))):
        return text
    try:
        return json.loads(text)
    except Exception:
        pass
    try:
        return ast.literal_eval(text)
    except Exception:
        return text


def _load_fast_mode_dataframe(prepared_path: Path) -> pd.DataFrame:
    """Читает подготовленный файл и приводит его к формату, который дальше
    проходит через тот же prepare_transactions/build_payload_from_agent_outputs,
    что и результат настоящего прогона агента."""
    raw = read_statement_table(prepared_path)
    raw = raw.rename(columns={c: FAST_MODE_COLUMN_ALIASES.get(c.strip(), c) for c in raw.columns})
    for col in FAST_MODE_STRUCTURED_COLUMNS:
        if col in raw.columns:
            raw[col] = raw[col].map(_parse_structured_cell)
    return raw


def _try_fast_mode(
    path: Path, *, progress: Any | None = None, status_callback: StatusCallback | None = None,
    court_filing_date: str = COURT_FILING_DATE_DEFAULT,
) -> dict[str, Any] | None:
    """Быстрый режим: если для загруженного файла уже есть подготовленный файл
    с таким же именем в FAST_MODE_DIR (выписка + готовые колонки анализа: тип
    операции, risk_level, правовая квалификация и т.д.), он проходит через тот
    же пайплайн подготовки/сборки таблиц, что и настоящий прогон, но БЕЗ вызова
    LLM -- анализ уже есть в файле. Наружу это выглядит как обычный прогон
    агента с дружелюбными сообщениями хода анализа, растянутыми на
    fast_mode_delay_seconds.
    """
    if not FAST_MODE_ENABLED:
        return None
    prepared_path = _fast_mode_result_path(path)
    if prepared_path is None:
        return None

    print(f"[agent] Быстрый режим: найден подготовленный файл для '{path.name}' -> {prepared_path.name}", flush=True)

    reporter = ProgressReporter(status_callback)
    delay = max(0.0, FAST_MODE_DELAY_SECONDS)
    n_steps = len(FAST_MODE_STEP_MESSAGES)
    per_step_delay = delay / n_steps if n_steps else 0.0

    def _tick(i: int, message: str) -> None:
        fraction = (i + 1) / n_steps
        reporter.report(message, fraction)
        if progress is not None:
            progress(fraction, desc=message)
        if per_step_delay:
            time.sleep(per_step_delay)

    _tick(0, FAST_MODE_STEP_MESSAGES[0])
    raw = _load_fast_mode_dataframe(prepared_path)

    _tick(1, FAST_MODE_STEP_MESSAGES[1])
    prepared, df_unique, cluster_strategy, diagnostics_df, warnings, rejected_df = prepare_transactions(
        raw, court_filing_date=court_filing_date,
    )

    _tick(2, FAST_MODE_STEP_MESSAGES[2])
    _tick(3, FAST_MODE_STEP_MESSAGES[3])
    # prepare_transactions только добавляет служебные колонки и никогда не убирает
    # чужие -- значит, operation_type/risk_level/legal_qualification/... из
    # подготовленного файла доходят до prepared как есть, без пересчёта.
    full_analysis = prepared

    _tick(4, FAST_MODE_STEP_MESSAGES[4])
    _tick(5, FAST_MODE_STEP_MESSAGES[5])
    payload = build_payload_from_agent_outputs(
        raw, prepared, df_unique, full_analysis, path.name, warnings,
        cluster_strategy=cluster_strategy, risk_db=default_risk_db, second_pass_updated=0,
    )
    payload.setdefault("meta", {})["mode"] = "fast_mode"
    payload["meta"]["sourceFile"] = path.name
    save_payload(payload, RUNTIME_PAYLOAD_PATH)

    head_preview = dataframe_head_preview_json(raw)
    print(f"[agent] Быстрый режим: обработано {len(raw)} операций из подготовленного файла.", flush=True)

    return {
        "filename": path.name,
        "rows": int(raw.shape[0]),
        "columns": int(raw.shape[1]),
        "head_text": head_preview,
        "payload": payload,
        "payload_path": str(RUNTIME_PAYLOAD_PATH),
        "mode": "fast_mode",
        "export_zip_path": "",
    }


def dataframe_head_preview_json(df, rows: int = 5) -> str:
    """JSON-предпросмотр для главной страницы Gradio.

    UI рендерит этот JSON как HTML-таблицу. В консоль по-прежнему печатается
    df.head().to_string(), чтобы отладка в терминале оставалась удобной.
    """
    head = df.head(rows).copy()
    head = head.astype(object).where(head.notna(), "")
    columns = [str(col) for col in head.columns]
    records = []
    for row in head.to_dict(orient="records"):
        records.append({str(key): str(value) for key, value in row.items()})
    return json.dumps({"columns": columns, "rows": records}, ensure_ascii=False, default=str)


def run_uploaded_statement(
    file_path: str | Path | None,
    *,
    test_mode: bool = False,
    progress: Any | None = None,
    use_real_agent: bool = True,
    status_callback: StatusCallback | None = None,
) -> dict[str, Any]:
    """Единый backend-entrypoint для Gradio.

    test_mode=True:
        Реальный LLM-агент не вызывается (детерминированный fallback-анализ вместо него),
        но выписка проходит через тот же pipeline и тот же контракт payload, что и в
        рабочем режиме — так тестовый прогон всегда отражает актуальную схему дашборда.

    test_mode=False:
        Файл читается, данные приводятся к контракту тетрадки агента, далее формируется
        dashboard_payload. Если рядом есть real_agent_entrypoint.py и use_real_agent=True,
        будет вызван настоящий LangGraph/LLM агент. Иначе используется безопасный fallback
        в том же формате, чтобы интерфейс оставался рабочим.
    """
    path = Path(file_path) if file_path else None
    print("\n" + "=" * 96, flush=True)
    print(f"[agent] Получен файл: {path if path is not None else 'файл не передан'}", flush=True)
    print(f"[agent] Тестовый режим: {'включен' if test_mode else 'выключен'}", flush=True)

    if test_mode:
        # Тестовый режим не требует файла от пользователя и не вызывает реальный LLM,
        # но использует тот же run_agent_pipeline(use_real_agent=False), что и обычный
        # fallback-прогон — payload гарантированно соответствует текущей схеме дашборда.
        preview_path = path if path is not None else (ASSETS / "mock_statement.xlsx")
        artifacts = run_agent_pipeline(preview_path, progress=progress, use_real_agent=False, status_callback=status_callback)
        artifacts.payload.setdefault("meta", {})["mode"] = "test_mock"
        artifacts.payload.setdefault("meta", {})["sourceFile"] = preview_path.name
        save_payload(artifacts.payload, RUNTIME_PAYLOAD_PATH)

        console_head_text = artifacts.input_df.head().to_string(index=False)
        head_preview = dataframe_head_preview_json(artifacts.input_df)
        print("[agent] Тестовый режим: LLM не вызывался, использован детерминированный fallback. Шапка выписки:", flush=True)
        print(console_head_text, flush=True)
        print("=" * 96 + "\n", flush=True)
        return {
            "filename": preview_path.name,
            "rows": int(artifacts.input_df.shape[0]),
            "columns": int(artifacts.input_df.shape[1]),
            "head_text": head_preview,
            "payload": artifacts.payload,
            "payload_path": str(RUNTIME_PAYLOAD_PATH),
            "mode": "test_mock",
            "export_zip_path": artifacts.exports_zip_path or "",
        }

    if path is None:
        raise ValueError("В рабочем режиме нужно передать Excel/CSV-файл.")

    fast_result = _try_fast_mode(path, progress=progress, status_callback=status_callback)
    if fast_result is not None:
        return fast_result

    artifacts = run_agent_pipeline(path, progress=progress, use_real_agent=use_real_agent, status_callback=status_callback)
    save_payload(artifacts.payload, RUNTIME_PAYLOAD_PATH)

    console_head_text = artifacts.input_df.head().to_string(index=False)
    head_preview = dataframe_head_preview_json(artifacts.input_df)
    analysis_head = artifacts.analysis_df.head().to_string(index=False) if not artifacts.analysis_df.empty else "Нет строк анализа"

    print("[agent] Анализ завершен. Шапка входной таблицы:", flush=True)
    print(console_head_text, flush=True)
    print("\n[agent] Шапка результата анализатора:", flush=True)
    print(analysis_head, flush=True)
    print(f"\n[agent] Payload сохранен: {RUNTIME_PAYLOAD_PATH}", flush=True)
    print("=" * 96 + "\n", flush=True)

    return {
        "filename": path.name,
        "rows": int(artifacts.input_df.shape[0]),
        "columns": int(artifacts.input_df.shape[1]),
        "head_text": head_preview,
        "analysis_head_text": analysis_head,
        "payload": artifacts.payload,
        "payload_path": str(RUNTIME_PAYLOAD_PATH),
        "mode": "real_agent" if use_real_agent else "fallback_agent_contract",
        "export_zip_path": artifacts.exports_zip_path or "",
    }


# Совместимость со старым app.py/ноутбуком.
def run_uploaded_statement_stub(file_path: str | Path, progress: Any | None = None, delay_sec: float = 0.0) -> dict[str, Any]:
    return run_uploaded_statement(file_path, test_mode=False, progress=progress)


# Удобный алиас для Jupyter: from agent_runner import run_agent
run_agent = run_uploaded_statement
