"""Очередь фоновых задач: загрузка выписки -> парсер -> анализ агентом -> сохранение. Одна задача за раз.

Прогресс и статус пишутся в meta.json анализа, поэтому интерфейс может опрашивать их таймером, а сотрудник — уйти
на другую страницу или закрыть вкладку: задача продолжится.
"""
from __future__ import annotations

import logging
import queue
import shutil
import threading
import time
import traceback
from pathlib import Path
from typing import Any

import pandas as pd

from .agent.common import parse_filing_date
from .agent.pipeline import AnalysisCancelled, run_analysis
from .agent.runtime import build_gigachat
from .analytics import enrich
from .config import SETTINGS
from .factory import build_runtime
from .parser.service import parse_statement_file
from .store import (ANALYZING, CANCELLED, DONE, ERROR, PARSING, STORE, AnalysisStore)

logger = logging.getLogger("finapp.jobs")

PARSE_SHARE = 0.12  # доля шкалы прогресса, отведённая на распознавание файла


class JobManager:
    def __init__(self, store: AnalysisStore = STORE):
        self.store = store
        self._queue: queue.Queue[str] = queue.Queue()
        self._cancel: dict[str, threading.Event] = {}
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    # ── постановка в очередь ──

    def submit(self, source_file: str | Path, filing_date: str, original_name: str | None = None) -> dict[str, Any]:
        """Копирует файл в хранилище и ставит анализ в очередь. ValueError — если дата заявления неверна."""
        parse_filing_date(filing_date)  # раннее и понятное сообщение об ошибке
        source = Path(source_file)
        meta = self.store.create(original_name or source.name, filing_date, size=source.stat().st_size)
        target = self.store.source_path(meta["id"], source.suffix.lower())
        shutil.copyfile(source, target)
        self.store.update_meta(meta["id"], source_suffix=source.suffix.lower())
        with self._lock:
            self._cancel[meta["id"]] = threading.Event()
        self._queue.put(meta["id"])
        self._ensure_worker()
        return meta

    def cancel(self, analysis_id: str) -> None:
        with self._lock:
            event = self._cancel.get(analysis_id)
        if event is not None:
            event.set()

    def _ensure_worker(self) -> None:
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._loop, name="finapp-jobs", daemon=True)
                self._thread.start()

    def _loop(self) -> None:
        while True:
            analysis_id = self._queue.get()
            try:
                self._run(analysis_id)
            except Exception:  # noqa: BLE001
                logger.exception("Задача %s упала", analysis_id)

    # ── выполнение ──

    def _run(self, analysis_id: str) -> None:
        store = self.store
        meta = store.get_meta(analysis_id)
        if meta is None:
            return
        cancel = self._cancel.get(analysis_id) or threading.Event()
        if cancel.is_set():
            store.update_meta(analysis_id, status=CANCELLED, message="Остановлено до начала", finished_at=_now())
            return
        settings = SETTINGS.get()

        def progress(fraction: float, message: str, status: str = ANALYZING) -> None:
            store.update_meta(analysis_id, status=status, progress=round(fraction, 3), message=message)

        def log(message: str) -> None:
            store.append_log(analysis_id, message)

        try:
            filing_date = parse_filing_date(meta["filing_date"])
            cfg = settings.agent_config(filing_date)
            rt = build_runtime(settings, cfg, log=log)
            mode = "gigachat" if rt.use_llm else "rules"
            store.update_meta(analysis_id, mode=mode)
            log("Режим: GigaChat-3-Ultra" if rt.use_llm else "Режим: предварительная оценка по правилам (GigaChat не подключён)")

            # 1. распознавание выписки
            progress(0.01, "Распознаю выписку", PARSING)
            parser_llm = None
            if rt.use_llm:
                try:
                    parser_llm = build_gigachat(settings.gigachat(), timeout=90)
                except Exception as exc:  # noqa: BLE001
                    log(f"GigaChat для парсера недоступен, работает алгоритм: {exc}")
            source = store.source_path(analysis_id, meta.get("source_suffix", ""))
            outcome = parse_statement_file(source, parser_llm, log=lambda m: store.append_log(analysis_id, "парсер " + m))
            if cancel.is_set():
                raise AnalysisCancelled()
            store.update_meta(analysis_id, org_name=outcome.owner_name, org_inn=outcome.owner_inn, n_ops=int(len(outcome.table)),
                              parse_status=outcome.status)
            log(f"Распознано операций: {len(outcome.table)}; {outcome.status_label}")
            progress(PARSE_SHARE, "Выписка распознана", ANALYZING)

            # 2. анализ
            def analysis_progress(fraction: float, message: str) -> None:
                progress(PARSE_SHARE + (1 - PARSE_SHARE) * fraction, message)

            result = run_analysis(outcome.table, rt, progress=analysis_progress, cancel=cancel)
            full = enrich(result.full_analysis)
            counts = full["status"].value_counts().to_dict()
            store.save_result(
                analysis_id, full, rejected=result.rejected, notes=result.notes, stats=result.stats,
                cluster_strategy=result.cluster_strategy, tokens=result.tokens, memory_update=result.memory_update,
                parse_summary=outcome.summary(), profile=result.profile)
            ops_dates = pd.to_datetime(full["date"], errors="coerce")
            store.update_meta(
                analysis_id, status=DONE, progress=1.0, message="Анализ завершён", finished_at=_now(),
                n_ops=int(len(full)), n_risky=int(counts.get("disputable", 0)), n_attention=int(counts.get("attention", 0)),
                n_unclassified=int(counts.get("unclassified", 0)),
                period_start=f"{ops_dates.min():%d.%m.%Y}" if ops_dates.notna().any() else "",
                period_end=f"{ops_dates.max():%d.%m.%Y}" if ops_dates.notna().any() else "",
                org_name=outcome.owner_name or meta.get("org_name") or "", org_inn=outcome.owner_inn or "",
                tokens=result.tokens, error="")
            log(f"Готово: операций {len(full)}, потенциально оспоримых {counts.get('disputable', 0)}")
        except AnalysisCancelled:
            store.update_meta(analysis_id, status=CANCELLED, message="Остановлено пользователем", finished_at=_now())
        except Exception as exc:  # noqa: BLE001
            logger.error("Анализ %s завершился ошибкой:\n%s", analysis_id, traceback.format_exc())
            store.update_meta(analysis_id, status=ERROR, message="Ошибка", error=f"{type(exc).__name__}: {exc}",
                              finished_at=_now())
        finally:
            with self._lock:
                self._cancel.pop(analysis_id, None)


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


JOBS = JobManager()
