from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger("bankruptcy_agent.models")

# =============================================================================
# USER MODEL CONFIG — ЗАПОЛНИТЬ САМОСТОЯТЕЛЬНО
# =============================================================================
# Это единственный обязательный ручной блок.
# В тетрадке этих объектов не было — ты создаешь их сам под свою среду.
#
# Минимум для реального запуска агентного анализа:
#   llm        — основная модель orchestrator/analyzer;
#   llm_helper — модель для генерации запросов к нормативке/судебной практике;
#   embeddings — embedding-модель для FAISS нормативки и судебной практики.
#
# Пример-заготовка:
#   from langchain_community.chat_models import GigaChat
#   from langchain_community.embeddings import GigaChatEmbeddings
#
#   llm = GigaChat(...)
#   llm_helper = llm
#   embeddings = GigaChatEmbeddings(...)
# =============================================================================

llm: Any | None = None
llm_helper: Any | None = None
embeddings: Any | None = None


# =============================================================================
# AUTO TOOL CONFIG — ГОТОВО ИЗ ТЕТРАДКИ
# =============================================================================
# Ниже не нужно ничего писать вручную для risk_db и practice_tool.
# Они создаются автоматически по логике из тетрадки:
#   risk_db       -> RiskMemoryDB("risk_memory.db")
#   practice_tool -> CourtPracticeSearchTool(...), если есть court_practice_storage/*
#
# retriever — единственный внешний источник, которого в тетрадке не было готовым.
# Если embeddings задан и рядом есть ./faiss_normative_db, он будет загружен.
# Если индекса нет, будет создан dummy FAISS-индекс, как в тетрадке.
# =============================================================================

RISK_MEMORY_DB_PATH = Path(os.getenv("RISK_MEMORY_DB_PATH", "risk_memory.db"))
NORMATIVE_INDEX_PATH = Path(os.getenv("NORMATIVE_INDEX_PATH", "faiss_normative_db"))
COURT_PRACTICE_STORAGE = Path(os.getenv("COURT_PRACTICE_STORAGE", "court_practice_storage"))


def _build_risk_db() -> Any | None:
    try:
        from .risk_memory import RiskMemoryDB
        return RiskMemoryDB(db_path=str(RISK_MEMORY_DB_PATH))
    except Exception as exc:
        logger.warning("risk_db не инициализирован: %s: %s", type(exc).__name__, exc)
        return None


def _build_retriever() -> Any | None:
    if embeddings is None:
        return None
    try:
        from .normative import build_normative_retriever
        vectorstore = build_normative_retriever(embeddings=embeddings, index_path=str(NORMATIVE_INDEX_PATH))
        return vectorstore.as_retriever(search_kwargs={"k": 4})
    except Exception as exc:
        logger.warning("retriever не инициализирован: %s: %s", type(exc).__name__, exc)
        return None


def _build_practice_tool() -> Any | None:
    if embeddings is None:
        return None
    try:
        from .court_practice import CourtPracticeSearchTool, court_practice_storage_is_available
        db_path = COURT_PRACTICE_STORAGE / "court_practice.sqlite"
        faiss_index_path = COURT_PRACTICE_STORAGE / "court_practice.faiss"
        faiss_meta_path = COURT_PRACTICE_STORAGE / "faiss_metadata.pkl"
        if not court_practice_storage_is_available(db_path, faiss_index_path, faiss_meta_path):
            logger.info("practice_tool не создан: папка судебной практики не найдена или неполная: %s", COURT_PRACTICE_STORAGE)
            return None
        return CourtPracticeSearchTool(
            embeddings=embeddings,
            db_path=db_path,
            faiss_index_path=faiss_index_path,
            faiss_meta_path=faiss_meta_path,
        )
    except Exception as exc:
        logger.warning("practice_tool не инициализирован: %s: %s", type(exc).__name__, exc)
        return None


risk_db: Any | None = _build_risk_db()
retriever: Any | None = _build_retriever()
practice_tool: Any | None = _build_practice_tool()
graph_tool: Any | None = None


@dataclass(slots=True)
class AgentRuntime:
    """Runtime-зависимости агента, которые в ноутбуке жили в globals()."""

    llm: Any
    llm_helper: Any | None = None
    embeddings: Any | None = None
    retriever: Any | None = None
    practice_tool: Any | None = None
    risk_db: Any | None = None
    graph_tool: Any | None = None


def rebuild_tool_runtime() -> None:
    """Пересобирает retriever/practice_tool/risk_db после ручной настройки моделей.

    Используй, если в интерактивной среде присвоил `embeddings` уже после импорта
    этого модуля. При обычном запуске app.py достаточно прописать llm/embeddings
    вверху этого файла до блока AUTO TOOL CONFIG.
    """
    global risk_db, retriever, practice_tool
    risk_db = _build_risk_db()
    retriever = _build_retriever()
    practice_tool = _build_practice_tool()


def build_runtime() -> AgentRuntime:
    """Собирает runtime для реального агента из переменных этого файла."""
    ensure_runtime_is_configured(strict_helper=False)
    return AgentRuntime(
        llm=llm,
        llm_helper=llm_helper,
        embeddings=embeddings,
        retriever=retriever,
        practice_tool=practice_tool,
        risk_db=risk_db,
        graph_tool=graph_tool,
    )


def ensure_runtime_is_configured(*, strict_helper: bool = False) -> None:
    """Падает явно только по реально обязательным LLM-зависимостям."""
    missing: list[str] = []
    if llm is None:
        missing.append("llm")
    if strict_helper and llm_helper is None:
        missing.append("llm_helper")
    if missing:
        raise RuntimeError(
            "Не настроены runtime-объекты реального агента: "
            + ", ".join(missing)
            + ". Заполните bankruptcy_agent/models.py. "
            "risk_db и practice_tool уже настраиваются автоматически; retriever зависит от embeddings и FAISS-индекса нормативки."
        )
