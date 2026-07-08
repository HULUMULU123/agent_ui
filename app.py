from __future__ import annotations

import inspect
import json
import os
import queue
import threading
from pathlib import Path
from typing import Any, Iterator

import gradio as gr

from agent_runner import run_uploaded_statement

ROOT = Path(__file__).resolve().parent
ASSETS = ROOT / "assets"

HTML = (ASSETS / "dashboard.html").read_text(encoding="utf-8")
CSS = (ASSETS / "styles.css").read_text(encoding="utf-8")
JS = (ASSETS / "dashboard.js").read_text(encoding="utf-8")

DEFAULT_DATA_FILE = ASSETS / "dashboard_payload.json"
RUNTIME_DATA_FILE = ASSETS / "dashboard_payload_runtime.json"
DATA_PATH = Path(os.getenv("DASHBOARD_DATA_PATH", str(RUNTIME_DATA_FILE if RUNTIME_DATA_FILE.exists() else DEFAULT_DATA_FILE))).expanduser()
if not DATA_PATH.is_absolute():
    DATA_PATH = (ROOT / DATA_PATH).resolve()
if not DATA_PATH.exists():
    DATA_PATH = DEFAULT_DATA_FILE

try:
    DATA = json.loads(DATA_PATH.read_text(encoding="utf-8"))
except FileNotFoundError:
    DATA = {
        "meta": {"appTitle": "Финансовый анализ", "appSubtitle": "агент банкротного риска операций"},
        "summary": {},
        "documents": [],
        "transactions": [],
        "charts": {},
        "signals": [],
        "legalReport": [],
        "counterpartyRegistry": [],
        "modal": {},
    }
DATA_JSON = json.dumps(DATA, ensure_ascii=False).replace("</", "<\\/")

HEAD = f"""
<meta name="viewport" content="width=device-width, initial-scale=1" />
<script>window.__FINANCE_DASHBOARD_DATA__ = {DATA_JSON};</script>
<script>{JS}</script>
"""


def _accepts(callable_obj: Any, name: str) -> bool:
    try:
        signature = inspect.signature(callable_obj)
    except (TypeError, ValueError):
        return False
    return name in signature.parameters


LAUNCH_ACCEPTS_CSS = _accepts(gr.Blocks.launch, "css")
BLOCKS_KWARGS: dict[str, Any] = {
    "title": "Финансовый анализ банковских выписок",
    "fill_width": True,
}
if not LAUNCH_ACCEPTS_CSS:
    BLOCKS_KWARGS.update({"css": CSS, "head": HEAD})


def _json_for_frontend(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, default=str)


def start_agent_analysis(
    uploaded_file: str | None,
    test_mode: bool,
    progress: gr.Progress = gr.Progress(track_tqdm=True),
) -> Iterator[tuple[str, str, str, str | None]]:
    """Gradio callback: файл + режим → payload для HTML/JS dashboard.

    Генератор: агент считается в фоновом потоке, а этот генератор параллельно
    вычитывает из очереди дружелюбные сообщения о ходе анализа и стримит их
    в тот же статус-textbox (формат "ANALYSIS_PROGRESS:{percent}:{message}").
    Так фронтенд получает реальный прогресс вместо статичной "заглушки на
    90%" — прогресс-бар и лог в модалке двигаются ровно так же, как двигается
    сам пайплайн: чтение файла -> выборка -> анализ кластеров -> распространение
    -> второй проход -> память риска -> сборка дашборда -> экспорт.

    В тестовом режиме файл необязателен: используется assets/mock_statement.xlsx.
    В рабочем режиме файл обязателен.
    """
    if not uploaded_file:
        if bool(test_mode):
            uploaded_file = str(ASSETS / "mock_statement.xlsx")
            print("[agent] Тестовый режим запущен без пользовательского файла; используется mock_statement.xlsx", flush=True)
        else:
            yield "ANALYSIS_ERROR: файл не выбран в backend bridge", gr.skip(), gr.skip(), gr.skip()
            return

    status_queue: queue.Queue[str | None] = queue.Queue()
    result_holder: dict[str, Any] = {}

    def on_status(message: str, percent: int) -> None:
        status_queue.put(f"ANALYSIS_PROGRESS:{percent}:{message}")

    def worker() -> None:
        try:
            result_holder["result"] = run_uploaded_statement(
                uploaded_file,
                test_mode=bool(test_mode),
                progress=progress,
                status_callback=on_status,
            )
        except Exception as exc:  # noqa: BLE001 — статус об ошибке уходит в UI, не молча теряется
            result_holder["error"] = exc
        finally:
            status_queue.put(None)

    worker_thread = threading.Thread(target=worker, daemon=True)
    worker_thread.start()

    while True:
        item = status_queue.get()
        if item is None:
            break
        yield item, gr.skip(), gr.skip(), gr.skip()

    worker_thread.join()

    if "error" in result_holder:
        exc = result_holder["error"]
        print(f"[agent] Ошибка анализа: {type(exc).__name__}: {exc}", flush=True)
        yield f"ANALYSIS_ERROR: {type(exc).__name__}: {exc}", gr.skip(), gr.skip(), gr.skip()
        return

    result = result_holder["result"]
    payload = result.get("payload", {})
    mode = result.get("mode", "unknown")
    payload.setdefault("meta", {})["analysisCompleted"] = True
    payload.setdefault("meta", {})["mode"] = mode
    payload.setdefault("meta", {})["sourceFile"] = result.get("filename", "")
    status = (
        f"ANALYSIS_DONE: режим={mode}; "
        f"{result['rows']} строк, {result['columns']} колонок. "
        f"Файл: {result['filename']}"
    )
    preview = result.get("head_text") or result.get("analysis_head_text", "")
    export_zip_path = result.get("export_zip_path") or None
    yield status, preview, _json_for_frontend(payload), export_zip_path


def start_agent_analysis_real(
    uploaded_file: str | None,
    progress: gr.Progress = gr.Progress(track_tqdm=True),
) -> Iterator[tuple[str, str, str, str | None]]:
    """Запуск реального агента: тестовый режим принудительно выключен."""
    yield from start_agent_analysis(uploaded_file, False, progress)


def start_agent_analysis_test(
    uploaded_file: str | None,
    progress: gr.Progress = gr.Progress(track_tqdm=True),
) -> Iterator[tuple[str, str, str, str | None]]:
    """Запуск mock-режима: агент не вызывается, файл необязателен."""
    yield from start_agent_analysis(uploaded_file, True, progress)


with gr.Blocks(**BLOCKS_KWARGS) as demo:
    gr.HTML(value=HTML, show_label=False, elem_id="finance-dashboard-html")

    # Скрытый backend-bridge. Кастомный HTML управляет этими компонентами через JS.
    with gr.Group(elem_id="agent-backend-bridge", elem_classes=["agent-backend-bridge"]):
        agent_file = gr.File(
            label="Файл для анализа агентом",
            file_types=[".xlsx", ".xls", ".csv"],
            type="filepath",
            elem_id="agent-file-upload",
        )
        # Две разные backend-кнопки надежнее, чем попытка прокидывать состояние
        # кастомного HTML-checkbox внутрь скрытого gr.Checkbox.
        # JS нажимает одну из них в зависимости от #test-mode-checkbox.
        agent_start_real = gr.Button("Начать реальный анализ", elem_id="agent-start-button")
        agent_start_test = gr.Button("Начать тестовый анализ", elem_id="agent-start-test-button")
        agent_status = gr.Textbox(label="Статус агента", elem_id="agent-run-status")
        agent_preview = gr.Textbox(label="head() результата", lines=10, elem_id="agent-run-preview")
        agent_payload = gr.Textbox(label="dashboard_payload", lines=2, elem_id="agent-run-payload")
        agent_export_zip = gr.File(label="Архив таблиц пайплайна", elem_id="agent-export-zip")

    agent_start_real.click(
        fn=start_agent_analysis_real,
        inputs=[agent_file],
        outputs=[agent_status, agent_preview, agent_payload, agent_export_zip],
        show_progress="full",
        api_name="start_analysis_real",
    )
    agent_start_test.click(
        fn=start_agent_analysis_test,
        inputs=[agent_file],
        outputs=[agent_status, agent_preview, agent_payload, agent_export_zip],
        show_progress="full",
        api_name="start_analysis_test",
    )


if __name__ == "__main__":
    server_name = os.getenv("GRADIO_SERVER_NAME", "127.0.0.1")
    server_port = int(os.getenv("GRADIO_SERVER_PORT", os.getenv("PORT", "7860")))
    launch_kwargs: dict[str, Any] = {
        "server_name": server_name,
        "server_port": server_port,
        "show_error": True,
    }
    if LAUNCH_ACCEPTS_CSS:
        launch_kwargs.update({"css": CSS, "head": HEAD})
    demo.launch(**launch_kwargs)
