"""Инструменты оркестратора: правовой поиск (нормы + судебная практика) и история риска контрагента."""
from __future__ import annotations

import json
import logging
import os
import pickle
import sqlite3
import threading
from pathlib import Path
from typing import Any

import numpy as np

from .common import (as_list, extract_json, normalize_to_list, truncate_long_strings)
from .prompts import LEGAL_MODULE_PROMPT, LEGAL_SELECTION_SYSTEM_PROMPT
from .risk_memory import RISK_GRADE_LABELS
from .runtime import Runtime, invoke_llm, make_messages

logger = logging.getLogger("finapp.agent.tools")

TOOL_SCHEMAS = [
    {
        "name": "search_practice_and_normative",
        "description": (
            "Подбирает нормы и позиции судов по описанию ситуации: что суды проверяют, какие документы требуют. "
            "Сам формирует поисковые запросы из задачи и контекста. Не более 2 вызовов на пакет операций. "
            "search_task — нейтральное описание фактов без юридической оценки, сумм, дат, названий, ИНН, ФИО и номеров "
            "договоров. Хорошо: «Перечисление денежных средств контрагенту в счёт ранее возникшего обязательства "
            "незадолго до возбуждения дела о банкротстве». Плохо: «Проверить основания для признания сделки "
            "недействительной». hypotheses — не более двух проверяемых гипотез. operation_context — контекст по одному "
            "объекту на операцию (category, direction, period, counterparty_category, ordinary_explanation, "
            "risk_question, confirmed_facts, unknown_facts, risk_signals, protective_signals, profile_signals, "
            "affiliation_signals). Возвращает нейтральный справочный материал, а не вывод."),
        "parameters": {
            "type": "object",
            "properties": {
                "search_task": {"type": "string", "description": "Нейтральное описание фактов ситуации"},
                "hypotheses": {"type": "array", "items": {"type": "string"}, "description": "Не более двух гипотез"},
                "operation_context": {"type": "array", "items": {"type": "object"}, "description": "Контекст операций"},
            },
            "required": ["search_task"],
        },
    },
    {
        "name": "get_conterparty_risk",
        "description": (
            "История риска контрагента по прошлым делам: сколько его операций отмечались для проверки, насколько "
            "недавно и почему. inn — counterparty_id операции (обезличенный идентификатор вида ORG_N): передавай как "
            "есть, реальный ИНН подставит система. Тип операции, наименование и назначение не передавай. "
            "История — дополнительный сигнал, а не готовый risk_level новой операции."),
        "parameters": {
            "type": "object",
            "properties": {"inn": {"type": "string", "description": "counterparty_id вида ORG_N"}},
            "required": ["inn"],
        },
    },
]

TOOL_NAMES = {schema["name"] for schema in TOOL_SCHEMAS}


# ───────────────────────── Судебная практика (FAISS + SQLite) ─────────────────────────

COURT_FILTER_COLUMNS = (
    "court_level", "act_type", "case_type", "operation_type", "timing_pattern",
    "counterparty_relation", "real_performance", "transaction_regular", "court_result",
)
COURT_TAG_FILTERS = ("hypotheses", "legal_sources", "important_documents", "missing_documents", "documents_to_request")


class CourtPracticeSearchTool:
    """statement -> FAISS -> case_id -> SQLite -> сжатая карточка. Без индекса возвращает пустой результат."""

    def __init__(self, storage: Path, embeddings: Any | None):
        self._lock = threading.Lock()
        self.embeddings = embeddings
        db_path = storage / "court_practice.sqlite"
        index_path = storage / "court_practice.faiss"
        meta_path = storage / "faiss_metadata.pkl"
        self.available = embeddings is not None and all(p.exists() for p in (db_path, index_path, meta_path))
        if not self.available:
            return
        import faiss  # noqa: PLC0415
        self._faiss = faiss
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.index = faiss.read_index(str(index_path))
        with open(meta_path, "rb") as f:  # индекс собран нами же; чужие pickle не загружать
            self.metadata = pickle.load(f)  # noqa: S301

    def _matches_filters(self, case_id: str, filters: dict | None) -> bool:
        if not filters:
            return True
        row = self.conn.execute(
            f"SELECT {', '.join(COURT_FILTER_COLUMNS)} FROM cases WHERE case_id = ?", (case_id,)).fetchone()
        if not row:
            return False
        for key, actual in zip(COURT_FILTER_COLUMNS, row):
            wanted = filters.get(key)
            if wanted and actual not in set(as_list(wanted)):
                return False
        for tag_type in COURT_TAG_FILTERS:
            wanted = filters.get(tag_type)
            if not wanted:
                continue
            actual_tags = {r[0] for r in self.conn.execute(
                "SELECT tag_value FROM case_tags WHERE case_id = ? AND tag_type = ?", (case_id, tag_type))}
            if actual_tags.isdisjoint(set(map(str, as_list(wanted)))):
                return False
        return True

    def search(self, statement: str, filters: dict | None = None, top_k: int = 5) -> dict:
        if not self.available or not statement.strip():
            return {"results": []}
        vector = np.array(self.embeddings.embed_documents([statement]), dtype="float32").reshape(1, -1)
        self._faiss.normalize_L2(vector)
        results, seen = [], set()
        with self._lock:
            _, ids = self.index.search(vector, min(top_k * 5, self.index.ntotal))
            for vector_id in ids[0]:
                if vector_id < 0:
                    continue
                case_id = self.metadata[int(vector_id)]["case_id"]
                if case_id in seen or not self._matches_filters(case_id, filters):
                    continue
                seen.add(case_id)
                row = self.conn.execute("SELECT full_json FROM cases WHERE case_id = ?", (case_id,)).fetchone()
                if not row:
                    continue
                card = json.loads(row[0])
                results.append({key: card.get(key) for key in ("case_summary", "risk_factors", "legal_logic", "evidence")})
                if len(results) >= top_k:
                    break
        return {"results": results}


def build_normative_retriever(embeddings: Any | None, index_path: Path, parent_store: Path) -> Any | None:
    """Поиск по нормативной базе (FAISS + ParentDocumentRetriever). None, если индекса или langchain нет."""
    if embeddings is None or not (index_path / "index.faiss").exists():
        return None
    try:
        from langchain_classic.retrievers import ParentDocumentRetriever
        from langchain_classic.storage import LocalFileStore
        from langchain_classic.storage._lc_store import create_kv_docstore
        from langchain_community.vectorstores import FAISS
        from langchain_text_splitters import RecursiveCharacterTextSplitter

        # Индекс собран нами же; pickle внутри FAISS.load_local из чужих источников не загружать.
        vectorstore = FAISS.load_local(str(index_path), embeddings, allow_dangerous_deserialization=True)
        return ParentDocumentRetriever(
            vectorstore=vectorstore,
            docstore=create_kv_docstore(LocalFileStore(str(parent_store))),
            child_splitter=RecursiveCharacterTextSplitter(chunk_size=300, chunk_overlap=50),
            parent_splitter=RecursiveCharacterTextSplitter(chunk_size=1500, chunk_overlap=200),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Нормативная база не подключена: %s: %s", type(exc).__name__, exc)
        return None


# ───────────────────────── Реализация инструментов ─────────────────────────

def search_case_law_practice(rt: Runtime, statement: str, top_k: int = 1) -> dict:
    if rt.practice_tool is None:
        return {"results": []}
    return truncate_long_strings(rt.practice_tool.search(statement, top_k=top_k), rt.cfg.legal_fragment_max_chars)


def search_normative_base(rt: Runtime, query: str, k: int = 2) -> str:
    if rt.retriever is None:
        return ""
    docs = rt.retriever.invoke(query) or []
    chunks = []
    for i, doc in enumerate(docs[:k], start=1):
        meta = doc.metadata or {}
        chunks.append("\n".join([
            f"[Фрагмент {i}]",
            f"Документ: {meta.get('law_name') or 'не указан'}",
            f"Статья: {meta.get('article_title') or 'не указана'}",
            f"Путь: {meta.get('path') or 'не указан'}",
            f"Текст: {truncate_long_strings(doc.page_content.strip(), rt.cfg.legal_fragment_max_chars)}",
        ]))
    return "\n---\n".join(chunks)


def _select_relevant_legal_context(rt: Runtime, search_task: str, law_practice: list, normative_base: list) -> dict | None:
    """Отбор и пересказ найденного отдельным нейтральным вызовом llm_helper; None — отбор не удался."""
    if not rt.cfg.legal_select_relevant or not (law_practice or normative_base):
        return None
    payload = json.dumps(
        {"question": search_task, "fragments": {"case_law_practice": law_practice, "normative_base": normative_base}},
        ensure_ascii=False)[:rt.cfg.legal_selection_max_input_chars]
    messages = make_messages(LEGAL_SELECTION_SYSTEM_PROMPT, payload)
    for attempt in range(1, rt.cfg.legal_selection_retries + 2):  # фильтр модели стохастичен: повтор часто проходит
        try:
            parsed = extract_json(invoke_llm(rt.helper, messages, role="legal-select"))
        except Exception as exc:  # noqa: BLE001
            logger.warning("Отбор правовых материалов, попытка %d: %s", attempt, exc)
            continue
        if isinstance(parsed, dict) and not parsed.get("_parse_error"):
            normative = parsed.get("normative")
            court_practice = parsed.get("court_practice")
            return {
                "normative": [n for n in normative if isinstance(n, dict)] if isinstance(normative, list) else [],
                "court_practice": [p for p in court_practice if isinstance(p, dict)] if isinstance(court_practice, list) else [],
            }
        logger.warning("Отбор правовых материалов: ответ не разобран, попытка %d.", attempt)
    return None


def search_practice_and_normative(rt: Runtime, search_task: str, hypotheses: list[str] | None = None,
                                  operation_context: list[dict] | None = None) -> str:
    context = {"search_task": search_task, "hypotheses": hypotheses or [], "operations_context": operation_context or []}
    empty = json.dumps({"normative": [], "court_practice": []}, ensure_ascii=False)
    if rt.helper is None or (rt.retriever is None and rt.practice_tool is None):
        return empty  # базы не подключены — моделей лишний раз не дёргаем
    try:
        response = invoke_llm(rt.helper, make_messages(LEGAL_MODULE_PROMPT, json.dumps(context, ensure_ascii=False)),
                              role="legal-queries")
    except Exception as exc:  # noqa: BLE001
        logger.error("Правовой модуль: ошибка вызова LLM: %s", exc)
        return empty

    queries = extract_json(response)
    queries = queries if isinstance(queries, dict) and not queries.get("_parse_error") else {}
    law_queries = normalize_to_list(queries.get("case_law_queries"))[:rt.cfg.legal_case_law_queries]
    norm_queries = normalize_to_list(queries.get("normative_queries"))[:rt.cfg.legal_normative_queries]
    logger.info("Правовой поиск: практика=%s, нормы=%s", law_queries, norm_queries)

    law_practice, normative_base = [], []
    for query in law_queries:
        try:
            law_practice.append(search_case_law_practice(rt, query))
        except Exception:  # noqa: BLE001
            logger.exception("Ошибка поиска судебной практики: %r", query[:100])
    for query in norm_queries:
        try:
            normative_base.append(search_normative_base(rt, query))
        except Exception:  # noqa: BLE001
            logger.exception("Ошибка поиска по нормативной базе: %r", query[:100])

    # Сырой правовой текст в основную модель не отдаём: он провоцирует отказы и пересказ «от себя».
    selected = _select_relevant_legal_context(rt, search_task, law_practice, normative_base)
    return json.dumps(selected or {"normative": [], "court_practice": []}, ensure_ascii=False)


def get_conterparty_risk(rt: Runtime, inn: str) -> dict:
    if rt.risk_db is None:
        return {"counterparty": inn, "history": "Память риска не подключена."}
    current = rt.risk_db.compute_current_risk(inn)
    if not current["events_count"]:
        return {"counterparty": inn, "history": "Отмеченных операций по контрагенту в прошлых делах нет."}
    reasons = [str(c["reason"])[:300] for c in current["top_contributions"] if c.get("reason")]
    return {
        "counterparty": inn,
        "history": (
            f"Операций контрагента, отмеченных для проверки в прошлых делах: {current['events_count']}, "
            f"из них подтверждены за последние полгода: {current['recent_events_180d']}. "
            f"Уровень исторического риска: {RISK_GRADE_LABELS[current['risk_grade']]}."
        ),
        "reasons": reasons,
    }


def run_tool(rt: Runtime, name: str, args: dict) -> Any:
    if name == "search_practice_and_normative":
        return search_practice_and_normative(
            rt, str(args.get("search_task") or ""), args.get("hypotheses"), args.get("operation_context"))
    if name == "get_conterparty_risk":
        return get_conterparty_risk(rt, str(args.get("inn") or ""))
    raise KeyError(name)


def default_storage_paths(root: Path) -> tuple[Path, Path, Path]:
    return (Path(os.getenv("NORMATIVE_INDEX_PATH", root / "faiss_normative_db")),
            Path(os.getenv("NORMATIVE_PARENT_STORE", root / "parent_store")),
            Path(os.getenv("COURT_PRACTICE_STORAGE", root / "court_practice_storage")))
