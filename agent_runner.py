from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pandas as pd
from tqdm.auto import tqdm


STUB_STEPS = [
    "Инициализация окружения агента",
    "Чтение входного файла",
    "Нормализация колонок",
    "Подготовка батча операций",
    "Заглушка риск-анализа",
    "Формирование результата",
]


def read_statement_table(file_path: str | Path) -> pd.DataFrame:
    """Читает Excel/CSV-файл банковской выписки.

    Это отдельная функция, чтобы ее можно было напрямую вызывать из Jupyter
    и из Gradio. Сейчас это минимальная заглушка: файл читается, а первые строки
    выводятся в консоль.
    """
    path = Path(file_path)
    suffix = path.suffix.lower()

    if suffix in {".xlsx", ".xls"}:
        return pd.read_excel(path)

    if suffix == ".csv":
        # sep=None + engine="python" позволяет pandas самому определить разделитель.
        return pd.read_csv(path, sep=None, engine="python")

    raise ValueError(f"Неподдерживаемый формат файла: {suffix}. Нужен .xlsx, .xls или .csv")


def run_uploaded_statement_stub(file_path: str | Path, progress: Any | None = None, delay_sec: float = 0.35) -> dict[str, Any]:
    """Тестовый entrypoint агента для Gradio.

    В будущем сюда переносится/подключается реальная функция из Jupyter:
    - чтение выписки;
    - запуск пайплайна агента;
    - формирование dashboard_payload.json.

    Сейчас функция делает только проверочный сценарий:
    1) показывает tqdm-прогресс;
    2) читает Excel/CSV;
    3) печатает df.head() в консоль;
    4) возвращает краткий статус в UI.
    """
    path = Path(file_path)
    print("\n" + "=" * 92, flush=True)
    print(f"[agent] Получен файл: {path}", flush=True)
    print("[agent] Запуск тестовой заглушки анализа", flush=True)

    df: pd.DataFrame | None = None

    for step_index, step_name in enumerate(tqdm(STUB_STEPS, desc="Загрузка агента", unit="step"), start=1):
        if progress is not None:
            progress((step_index - 1) / len(STUB_STEPS), desc=step_name)

        if step_name == "Чтение входного файла":
            df = read_statement_table(path)

        time.sleep(delay_sec)

    if df is None:
        df = read_statement_table(path)

    if progress is not None:
        progress(1.0, desc="Анализ завершен")

    head = df.head()
    head_text = head.to_string(index=False)

    print("[agent] Анализ завершен. Шапка таблицы:", flush=True)
    print(head_text, flush=True)
    print("=" * 92 + "\n", flush=True)

    return {
        "filename": path.name,
        "rows": int(df.shape[0]),
        "columns": int(df.shape[1]),
        "head_text": head_text,
    }


# Удобный алиас для ноутбука: from agent_runner import run_agent
run_agent = run_uploaded_statement_stub
