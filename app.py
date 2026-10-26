"""Точка входа: интерфейс сотрудника «Банкротный анализ».

    python app.py            # http://127.0.0.1:7860
    GRADIO_SERVER_NAME=0.0.0.0 GRADIO_SERVER_PORT=8080 python app.py

Прежняя версия интерфейса (HTML-дашборд поверх старого пакета bankruptcy_agent) сохранена в app_legacy.py.
"""
from __future__ import annotations

import logging
import os

from finapp.ui.app import build_app

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s")

demo = build_app()

if __name__ == "__main__":
    demo.queue(default_concurrency_limit=8).launch(
        server_name=os.getenv("GRADIO_SERVER_NAME", "127.0.0.1"),
        server_port=int(os.getenv("GRADIO_SERVER_PORT", os.getenv("PORT", "7860"))),
        show_error=True,
        inbrowser=False,
    )
