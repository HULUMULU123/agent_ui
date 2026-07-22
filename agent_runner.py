from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from tqdm.auto import tqdm

from bankruptcy_agent import run_agent_pipeline, save_payload
from bankruptcy_agent.config import FAST_MODE_DELAY_SECONDS, FAST_MODE_DIR, FAST_MODE_ENABLED
from bankruptcy_agent.progress_utils import StatusCallback

ROOT = Path(__file__).resolve().parent
ASSETS = ROOT / "assets"
RUNTIME_PAYLOAD_PATH = ASSETS / "dashboard_payload_runtime.json"


def _fast_mode_result_path(path: Path) -> Path:
    return FAST_MODE_DIR / f"{path.stem}.json"


def _try_fast_mode(
    path: Path, *, progress: Any | None = None, status_callback: StatusCallback | None = None,
) -> dict[str, Any] | None:
    """Быстрый режим: если для загруженного файла уже есть подготовленный payload
    с таким же именем в FAST_MODE_DIR, отдаёт его после искусственной задержки
    вместо реального прогона агента (для демонстраций на заранее подготовленных
    примерах, без ожидания реального времени работы LLM-конвейера).
    """
    if not FAST_MODE_ENABLED:
        return None
    prepared_path = _fast_mode_result_path(path)
    if not prepared_path.exists():
        return None

    try:
        payload = json.loads(prepared_path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"[agent] Быстрый режим: не удалось прочитать подготовленный payload {prepared_path}: {exc}", flush=True)
        return None

    print(f"[agent] Быстрый режим: найден подготовленный результат для '{path.name}' -> {prepared_path.name}", flush=True)

    delay = max(0.0, FAST_MODE_DELAY_SECONDS)
    steps = 5
    for i in range(steps):
        fraction = (i + 1) / steps
        if status_callback is not None:
            status_callback("Загружаю подготовленный результат анализа" if fraction < 1 else "Готово", fraction)
        if progress is not None:
            progress(fraction, desc="Быстрый режим: загрузка подготовленного результата")
        if delay:
            time.sleep(delay / steps)

    payload.setdefault("meta", {})["mode"] = "fast_mode"
    payload["meta"]["sourceFile"] = path.name
    save_payload(payload, RUNTIME_PAYLOAD_PATH)

    summary = payload.get("summary", {})
    return {
        "filename": path.name,
        "rows": int(summary.get("inputRows", 0) or 0),
        "columns": int(summary.get("inputColumns", 0) or 0),
        "head_text": json.dumps({"columns": [], "rows": []}, ensure_ascii=False),
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
