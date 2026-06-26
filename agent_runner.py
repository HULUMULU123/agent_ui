from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tqdm.auto import tqdm

from bankruptcy_agent import read_statement_table, run_agent_pipeline, save_payload

ROOT = Path(__file__).resolve().parent
ASSETS = ROOT / "assets"
MOCK_PAYLOAD_PATH = ASSETS / "dashboard_payload.json"
RUNTIME_PAYLOAD_PATH = ASSETS / "dashboard_payload_runtime.json"


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


def load_mock_payload() -> dict[str, Any]:
    """Возвращает демонстрационные данные без запуска агента."""
    return json.loads(MOCK_PAYLOAD_PATH.read_text(encoding="utf-8"))


def run_uploaded_statement(
    file_path: str | Path | None,
    *,
    test_mode: bool = False,
    progress: Any | None = None,
    use_real_agent: bool = True,
) -> dict[str, Any]:
    """Единый backend-entrypoint для Gradio.

    test_mode=True:
        Агент не запускается. UI получает заранее подготовленный mock payload.

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
        # В тестовом режиме реальный агент и LLM/embeddings не запускаются.
        # Если пользовательский файл не передан, используем встроенную тестовую выписку
        # только для формирования df.head() на главной странице.
        preview_path = path if path is not None else (ASSETS / "mock_statement.xlsx")
        if progress is not None:
            for i, step in enumerate(tqdm(["Загрузка mock payload", "Обновление UI"], desc="Тестовый режим", unit="step"), start=1):
                progress((i - 1) / 2, desc=step)
        payload = load_mock_payload()
        payload.setdefault("meta", {})["mode"] = "test_mock"
        payload.setdefault("meta", {})["sourceFile"] = preview_path.name
        df = read_statement_table(preview_path)
        console_head_text = df.head().to_string(index=False)
        head_preview = dataframe_head_preview_json(df)
        print("[agent] Тестовый режим: агент не запускался. Шапка загруженного файла:", flush=True)
        print(console_head_text, flush=True)
        if progress is not None:
            progress(1.0, desc="Mock payload готов")
        print("=" * 96 + "\n", flush=True)
        return {
            "filename": preview_path.name,
            "rows": int(df.shape[0]),
            "columns": int(df.shape[1]),
            "head_text": head_preview,
            "payload": payload,
            "payload_path": str(MOCK_PAYLOAD_PATH),
            "mode": "test_mock",
        }

    if path is None:
        raise ValueError("В рабочем режиме нужно передать Excel/CSV-файл.")

    artifacts = run_agent_pipeline(path, progress=progress, use_real_agent=use_real_agent)
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
    }


# Совместимость со старым app.py/ноутбуком.
def run_uploaded_statement_stub(file_path: str | Path, progress: Any | None = None, delay_sec: float = 0.0) -> dict[str, Any]:
    return run_uploaded_statement(file_path, test_mode=False, progress=progress)


# Удобный алиас для Jupyter: from agent_runner import run_agent
run_agent = run_uploaded_statement
