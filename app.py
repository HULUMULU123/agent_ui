from __future__ import annotations

import inspect
import json
import os
from pathlib import Path
from typing import Any

import gradio as gr

from agent_runner import run_uploaded_statement_stub

ROOT = Path(__file__).resolve().parent
ASSETS = ROOT / "assets"

HTML = (ASSETS / "dashboard.html").read_text(encoding="utf-8")
CSS = (ASSETS / "styles.css").read_text(encoding="utf-8")
JS = (ASSETS / "dashboard.js").read_text(encoding="utf-8")

DEFAULT_DATA_FILE = ASSETS / "dashboard_payload.json"
LEGACY_DATA_FILE = ASSETS / "data.json"
DATA_PATH = Path(os.getenv("DASHBOARD_DATA_PATH", str(DEFAULT_DATA_FILE))).expanduser()
if not DATA_PATH.is_absolute():
    DATA_PATH = (ROOT / DATA_PATH).resolve()
if not DATA_PATH.exists() and DATA_PATH == DEFAULT_DATA_FILE and LEGACY_DATA_FILE.exists():
    DATA_PATH = LEGACY_DATA_FILE

try:
    DATA = json.loads(DATA_PATH.read_text(encoding="utf-8"))
except FileNotFoundError:
    DATA = {
        "meta": {"appTitle": "Финансовый анализ", "appSubtitle": "банковских выписок"},
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


# Gradio 5: css/head должны быть в Blocks().
# Gradio 6: css/head перенесены в launch().
# Поэтому параметры прокидываются по фактической сигнатуре установленной версии.
LAUNCH_ACCEPTS_CSS = _accepts(gr.Blocks.launch, "css")
BLOCKS_KWARGS: dict[str, Any] = {
    "title": "Финансовый анализ банковских выписок",
    "fill_width": True,
}
if not LAUNCH_ACCEPTS_CSS:
    BLOCKS_KWARGS.update({"css": CSS, "head": HEAD})



def start_agent_analysis(uploaded_file: str | None, progress: gr.Progress = gr.Progress(track_tqdm=True)) -> tuple[str, str]:
    """Gradio callback.

    uploaded_file приходит из скрытого gr.File. Основная логика вынесена в
    agent_runner.py, чтобы ее можно было заменить кодом из Jupyter без правки UI.
    """
    if not uploaded_file:
        return "ANALYSIS_ERROR: файл не выбран в backend bridge", ""

    try:
        result = run_uploaded_statement_stub(uploaded_file, progress=progress)
    except Exception as exc:  # Ошибку важно вернуть и в UI, и в консоль.
        print(f"[agent] Ошибка анализа: {exc}", flush=True)
        return f"ANALYSIS_ERROR: {type(exc).__name__}: {exc}", ""

    status = f"ANALYSIS_DONE: {result['rows']} строк, {result['columns']} колонок. Файл: {result['filename']}"
    return status, result.get("head_text", "")


with gr.Blocks(**BLOCKS_KWARGS) as demo:
    gr.HTML(value=HTML, show_label=False, elem_id="finance-dashboard-html")

    # Скрытый backend-bridge. Кастомная HTML-кнопка нажимает эту кнопку через JS,
    # а gr.File хранит файл так, как ожидает Gradio/Python callback.
    with gr.Group(elem_id="agent-backend-bridge", elem_classes=["agent-backend-bridge"]):
        agent_file = gr.File(
            label="Файл для анализа агентом",
            file_types=[".xlsx", ".xls", ".csv"],
            type="filepath",
            elem_id="agent-file-upload",
        )
        agent_start = gr.Button("Начать анализ", elem_id="agent-start-button")
        agent_status = gr.Textbox(label="Статус агента", elem_id="agent-run-status")
        agent_preview = gr.Textbox(label="head() таблицы", lines=8, elem_id="agent-run-preview")

    agent_start.click(
        fn=start_agent_analysis,
        inputs=[agent_file],
        outputs=[agent_status, agent_preview],
        show_progress="full",
        api_name="start_analysis",
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
