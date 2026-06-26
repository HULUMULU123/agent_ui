from __future__ import annotations

import json
import pickle
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np


BASE_DIR_COURT = Path("court_practice_storage")
DB_PATH_COURT = BASE_DIR_COURT / "court_practice.sqlite"
FAISS_INDEX_PATH_COURT = BASE_DIR_COURT / "court_practice.faiss"
FAISS_META_PATH_COURT = BASE_DIR_COURT / "faiss_metadata.pkl"


def _listify(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return [value]


def court_practice_storage_is_available(
    db_path: str | Path = DB_PATH_COURT,
    faiss_index_path: str | Path = FAISS_INDEX_PATH_COURT,
    faiss_meta_path: str | Path = FAISS_META_PATH_COURT,
) -> bool:
    """Проверяет, доступны ли файлы индекса судебной практики из тетрадки."""
    return Path(db_path).exists() and Path(faiss_index_path).exists() and Path(faiss_meta_path).exists()


def embed_texts(texts: list[str], embeddings_model: Any, normalize: bool = True) -> np.ndarray:
    """Возвращает embedding-векторы float32. Для cosine similarity нормализует L2."""
    try:
        import faiss
    except ImportError as exc:
        raise ImportError("Для поиска судебной практики нужен пакет faiss-cpu.") from exc

    vectors = embeddings_model.embed_documents(texts)
    vectors = np.array(vectors, dtype="float32")
    if vectors.ndim == 1:
        vectors = vectors.reshape(1, -1)
    if normalize:
        faiss.normalize_L2(vectors)
    return vectors


def fetch_cases_by_ids(conn: sqlite3.Connection, case_ids: list[str]) -> dict[str, dict[str, Any]]:
    if not case_ids:
        return {}
    placeholders = ",".join("?" for _ in case_ids)
    rows = conn.execute(
        f"SELECT case_id, full_json FROM cases WHERE case_id IN ({placeholders})",
        case_ids,
    ).fetchall()
    return {case_id: json.loads(full_json) for case_id, full_json in rows}


def case_matches_filters(conn: sqlite3.Connection, case_id: str, filters: Optional[dict[str, Any]]) -> bool:
    if not filters:
        return True

    row = conn.execute(
        """
        SELECT court_level, act_type, case_type, operation_type, timing_pattern,
               counterparty_relation, real_performance, transaction_regular,
               court_result
        FROM cases WHERE case_id = ?
        """,
        (case_id,),
    ).fetchone()
    if not row:
        return False

    direct_filters = {
        "court_level": row[0],
        "act_type": row[1],
        "case_type": row[2],
        "operation_type": row[3],
        "timing_pattern": row[4],
        "counterparty_relation": row[5],
        "real_performance": row[6],
        "transaction_regular": row[7],
        "court_result": row[8],
    }
    for key, actual in direct_filters.items():
        wanted = filters.get(key)
        if wanted and actual not in set(_listify(wanted)):
            return False

    tag_filter_keys = [
        "hypotheses",
        "legal_sources",
        "important_documents",
        "missing_documents",
        "documents_to_request",
    ]
    for tag_type in tag_filter_keys:
        wanted = filters.get(tag_type)
        if not wanted:
            continue
        wanted_set = set(map(str, _listify(wanted)))
        rows = conn.execute(
            "SELECT tag_value FROM case_tags WHERE case_id = ? AND tag_type = ?",
            (case_id, tag_type),
        ).fetchall()
        actual_set = {r[0] for r in rows}
        if actual_set.isdisjoint(wanted_set):
            return False
    return True


def compact_case_for_agent(card: dict[str, Any], score: float) -> dict[str, Any]:
    """Сжатый слой судебной практики для LLM, без сырого судебного акта."""
    return {
        "score": round(float(score), 4),
        "case_summary": card.get("case_summary"),
        "risk_factors": card.get("risk_factors"),
        "legal_logic": card.get("legal_logic"),
        "evidence": card.get("evidence"),
    }


class CourtPracticeSearchTool:
    """Поиск судебной практики из FAISS + SQLite, перенесенный из тетрадки.

    Ожидает рядом с проектом папку `court_practice_storage`:
    - court_practice.sqlite
    - court_practice.faiss
    - faiss_metadata.pkl
    """

    def __init__(
        self,
        embeddings: Any,
        db_path: str | Path = DB_PATH_COURT,
        faiss_index_path: str | Path = FAISS_INDEX_PATH_COURT,
        faiss_meta_path: str | Path = FAISS_META_PATH_COURT,
    ):
        if embeddings is None:
            raise RuntimeError("Для CourtPracticeSearchTool нужна embeddings-модель.")
        try:
            import faiss
        except ImportError as exc:
            raise ImportError("Для CourtPracticeSearchTool нужен пакет faiss-cpu.") from exc

        self.embeddings = embeddings
        self.db_path = Path(db_path)
        self.faiss_index_path = Path(faiss_index_path)
        self.faiss_meta_path = Path(faiss_meta_path)

        missing = [p for p in [self.db_path, self.faiss_index_path, self.faiss_meta_path] if not p.exists()]
        if missing:
            raise FileNotFoundError(
                "Не найдены файлы судебной практики: " + ", ".join(str(p) for p in missing)
            )

        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.index = faiss.read_index(str(self.faiss_index_path))
        with open(self.faiss_meta_path, "rb") as f:
            self.metadata = pickle.load(f)

    def search(
        self,
        statement: str,
        filters: Optional[dict[str, Any]] = None,
        top_k: int = 5,
        faiss_multiplier: int = 5,
    ) -> dict[str, Any]:
        if not statement or not statement.strip():
            raise ValueError("statement не должен быть пустым")
        if getattr(self.index, "ntotal", 0) <= 0:
            return {
                "statement": statement,
                "filters": filters or {},
                "top_k": top_k,
                "results_count": 0,
                "results": [],
                "limitations": ["FAISS-индекс судебной практики пуст"],
            }

        query_vec = embed_texts([statement], embeddings_model=self.embeddings, normalize=True)
        n_candidates = min(max(top_k * faiss_multiplier, top_k), self.index.ntotal)
        scores, ids = self.index.search(query_vec, n_candidates)

        results: list[dict[str, Any]] = []
        seen_case_ids: set[str] = set()
        for score, vector_id in zip(scores[0], ids[0]):
            if int(vector_id) < 0:
                continue
            meta = self.metadata[int(vector_id)]
            case_id = str(meta.get("case_id"))
            if not case_id or case_id in seen_case_ids:
                continue
            seen_case_ids.add(case_id)
            if not case_matches_filters(self.conn, case_id, filters):
                continue
            card = fetch_cases_by_ids(self.conn, [case_id]).get(case_id)
            if not card:
                continue
            results.append(compact_case_for_agent(card, float(score)))
            if len(results) >= top_k:
                break

        return {
            "statement": statement,
            "filters": filters or {},
            "top_k": top_k,
            "results_count": len(results),
            "results": results,
        }

    def close(self) -> None:
        self.conn.close()


def build_court_practice_tool_if_available(embeddings: Any) -> CourtPracticeSearchTool | None:
    """Создает practice_tool, если индекс судебной практики действительно лежит на диске."""
    if embeddings is None:
        return None
    if not court_practice_storage_is_available():
        return None
    return CourtPracticeSearchTool(embeddings=embeddings)
