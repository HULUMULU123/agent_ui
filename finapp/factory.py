"""Сборка Runtime агента из настроек: клиенты GigaChat-3-Ultra, память риска, нормативная база и судебная практика."""
from __future__ import annotations

import logging
import threading
from typing import Any

from .agent.common import AgentConfig
from .agent.risk_memory import RiskMemoryDB
from .agent.runtime import (Runtime, build_embeddings, build_gigachat)
from .agent.tools import CourtPracticeSearchTool, build_normative_retriever, default_storage_paths
from .config import RISK_DB_PATH, ROOT, AppSettings

logger = logging.getLogger("finapp.factory")

_lock = threading.Lock()
_risk_db: RiskMemoryDB | None = None
_legal_cache: dict[tuple, tuple[Any, Any]] = {}


def get_risk_db() -> RiskMemoryDB | None:
    global _risk_db
    with _lock:
        if _risk_db is None:
            try:
                _risk_db = RiskMemoryDB(RISK_DB_PATH)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Память риска недоступна: %s", exc)
        return _risk_db


def legal_sources_status(settings: AppSettings | None = None) -> dict[str, bool]:
    """Подключены ли нормативная база и судебная практика (по наличию файлов индексов)."""
    norm_path, _, court_path = default_storage_paths(ROOT)
    return {"normative": (norm_path / "index.faiss").exists(),
            "court_practice": all((court_path / n).exists() for n in
                                  ("court_practice.sqlite", "court_practice.faiss", "faiss_metadata.pkl"))}


def build_runtime(settings: AppSettings, cfg: AgentConfig, log=lambda message: None) -> Runtime:  # noqa: ANN001
    """llm=None (режим «по правилам»), если GigaChat не настроен или выбран режим rules."""
    risk_db = get_risk_db()
    if not settings.llm_enabled:
        return Runtime(cfg=cfg, risk_db=risk_db, log=log)
    gc = settings.gigachat()
    rt = Runtime(cfg=cfg, risk_db=risk_db, log=log)
    rt.llm = build_gigachat(gc, callbacks=[rt.usage_llm])
    rt.llm_helper = build_gigachat(gc, callbacks=[rt.usage_helper])
    rt.embeddings = build_embeddings(gc)
    norm_path, parent_store, court_path = default_storage_paths(ROOT)
    key = (str(norm_path), str(court_path), gc.base_url, gc.credentials)
    with _lock:
        cached = _legal_cache.get(key)
    if cached is None:
        retriever = build_normative_retriever(rt.embeddings, norm_path, parent_store)
        practice = CourtPracticeSearchTool(court_path, rt.embeddings)
        cached = (retriever, practice if practice.available else None)
        with _lock:
            _legal_cache[key] = cached
    rt.retriever, rt.practice_tool = cached
    return rt


def check_gigachat(settings: AppSettings) -> tuple[bool, str]:
    """Проверка соединения (кнопка на странице «Настройки»)."""
    gc = settings.gigachat()
    if not gc.configured:
        return False, f"GigaChat не настроен: {gc.problem()}."
    try:
        client = build_gigachat(gc, timeout=60)
        answer = client.invoke("Ответь 'ok' одним словом")
        return True, f"Соединение установлено, модель {gc.model} ответила: {str(getattr(answer, 'content', answer))[:80]}"
    except ImportError:
        return False, "Не установлен пакет langchain-gigachat (pip install -r requirements.txt)."
    except Exception as exc:  # noqa: BLE001
        return False, f"Ошибка соединения: {type(exc).__name__}: {str(exc)[:300]}"
