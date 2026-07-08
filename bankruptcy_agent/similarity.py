from __future__ import annotations

import logging
import math
from typing import Any

import numpy as np
import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from .config import (
    GRAPH_CONNECTIONS_COLUMN,
    PROPAGATION_CONFIDENCE_LOW,
    SIMILARITY_CONTEXT_WEIGHT,
    SIMILARITY_NUMERIC_FEATURES,
    SIMILARITY_NUMERIC_WEIGHT,
    SIMILARITY_TEXT_WEIGHT,
)

logger = logging.getLogger("bankruptcy_agent.similarity")

# Портировано из notebooks/agent_v3_adaptive_clusters_1.ipynb:
# "Временные признаки и адаптивная стратегия анализа кластеров" (ячейки 26-29).
#
# Единое метрическое пространство для трех задач: (1) определение плотности
# кластера, (2) выбор представителей, (3) распространение объяснений.


def resolve_counterparty_inn(row: pd.Series | dict) -> str | None:
    """Определяет ИНН контрагента операции.

    Допущение прототипа: знак суммы кодирует направление (отрицательная сумма —
    убытие средств у должника, контрагент — credit-сторона; положительная —
    прибытие, контрагент — debit-сторона). Если знак не информативен, приоритет
    отдается credit_inn.
    """
    get = row.get if hasattr(row, "get") else row.__getitem__
    credit_inn = str(get("credit_inn") or "").strip()
    debit_inn = str(get("debit_inn") or "").strip()
    amount = get("amount")

    def _valid(inn: str) -> bool:
        return inn.isdigit() and len(inn) in (10, 12)

    if amount is not None and pd.notna(amount):
        if amount < 0 and _valid(credit_inn):
            return credit_inn
        if amount > 0 and _valid(debit_inn):
            return debit_inn

    if _valid(credit_inn):
        return credit_inn
    if _valid(debit_inn):
        return debit_inn
    return None


def _prepare_numeric_block(frame: pd.DataFrame) -> pd.DataFrame:
    """Готовит числовой блок признаков (включая лог-сжатую сумму)."""
    block = pd.DataFrame(index=frame.index)
    for col in ("anomaly_score", "anomaly_model_score", "interval_days"):
        block[col] = pd.to_numeric(frame.get(col), errors="coerce") if col in frame.columns else np.nan
    block["amount_log"] = np.log1p(pd.to_numeric(frame.get("amount"), errors="coerce").abs())
    return block


def _text_similarity(a_texts: pd.Series, b_texts: pd.Series) -> np.ndarray:
    """Косинусное сходство текстов по TF-IDF символьных n-грамм (3-5)."""
    a = a_texts.fillna("").astype(str).str.lower().tolist()
    b = b_texts.fillna("").astype(str).str.lower().tolist()
    if not any(t.strip() for t in a + b):
        return np.full((len(a), len(b)), 0.5)
    vectorizer = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), min_df=1, use_idf=False)
    try:
        matrix = vectorizer.fit_transform(a + b)
    except ValueError:
        return np.full((len(a), len(b)), 0.5)
    return cosine_similarity(matrix[: len(a)], matrix[len(a):])


class SimilaritySpace:
    """Держит глобальные границы нормализации числовых признаков по выписке.

    Границы фиксируются один раз по всей выписке (робастные квантили 1%/99%),
    а не пересчитываются для каждой пары выборок — иначе нормализация внутри
    маленькой пары растягивала бы крошечные различия на весь единичный куб.
    """

    def __init__(self) -> None:
        self.bounds: dict[str, tuple[float, float]] | None = None

    def fit(self, reference_df: pd.DataFrame) -> None:
        block = _prepare_numeric_block(reference_df)
        bounds = {}
        for col in block.columns:
            low, high = block[col].quantile(0.01), block[col].quantile(0.99)
            if pd.isna(low) or pd.isna(high) or high <= low:
                low, high = 0.0, 1.0
            bounds[col] = (float(low), float(high))
        self.bounds = bounds
        logger.info("Границы нормализации признаков сходства зафиксированы: %s", bounds)

    def _scale_numeric(self, frame: pd.DataFrame) -> np.ndarray:
        if self.bounds is None:
            raise RuntimeError("Сначала вызовите fit(df) по всей выписке.")
        block = _prepare_numeric_block(frame)
        scaled = pd.DataFrame(index=block.index)
        for col, (low, high) in self.bounds.items():
            scaled[col] = ((block[col] - low) / (high - low)).clip(0.0, 1.0)
        scaled = scaled.fillna(scaled.median()).fillna(0.5)
        return scaled.to_numpy(dtype=float)

    def numeric_similarity(self, a_df: pd.DataFrame, b_df: pd.DataFrame) -> np.ndarray:
        """1 - нормированное евклидово расстояние в глобальной min-max шкале."""
        x_a = self._scale_numeric(a_df)
        x_b = self._scale_numeric(b_df)
        dists = np.sqrt(((x_a[:, None, :] - x_b[None, :, :]) ** 2).sum(axis=2))
        return 1.0 - dists / math.sqrt(x_a.shape[1])


_SPACE = SimilaritySpace()


def fit_similarity_space(reference_df: pd.DataFrame) -> None:
    """Фиксирует глобальные границы числовых признаков по всей выписке."""
    _SPACE.fit(reference_df)


def _connections_text(frame: pd.DataFrame) -> pd.Series:
    """Нормализует описание графовых связей к строке ('' — связей нет)."""
    if GRAPH_CONNECTIONS_COLUMN not in frame.columns:
        return pd.Series([""] * len(frame), index=frame.index)

    def norm(value: Any) -> str:
        if value is None or (isinstance(value, float) and pd.isna(value)):
            return ""
        if isinstance(value, (list, tuple, set)):
            return " ; ".join(map(str, value))
        return str(value)

    return frame[GRAPH_CONNECTIONS_COLUMN].map(norm)


def _context_similarity(a_df: pd.DataFrame, b_df: pd.DataFrame) -> np.ndarray:
    """Контрагентско-графовый блок сходства (веса поровну между компонентами):

    1. Совпадение контрагента: 1.0 — тот же ИНН; 0.0 — разные; 0.5 — ИНН не
       определен хотя бы у одной стороны (нейтрально).
    2. Совпадение картины связей: обе операции без связей — 1.0; связи есть
       только у одной — 0.0; связи есть у обеих — TF-IDF-сходство описаний.
    """
    a_cp = [resolve_counterparty_inn(row) for _, row in a_df.iterrows()]
    b_cp = [resolve_counterparty_inn(row) for _, row in b_df.iterrows()]
    cp = np.empty((len(a_cp), len(b_cp)))
    for i, x in enumerate(a_cp):
        for j, y in enumerate(b_cp):
            if x is None or y is None:
                cp[i, j] = 0.5
            else:
                cp[i, j] = 1.0 if x == y else 0.0

    a_conn = _connections_text(a_df)
    b_conn = _connections_text(b_df)
    a_has = a_conn.str.strip().astype(bool).to_numpy()
    b_has = b_conn.str.strip().astype(bool).to_numpy()

    conn = np.zeros((len(a_conn), len(b_conn)))
    both_empty = (~a_has[:, None]) & (~b_has[None, :])
    conn[both_empty] = 1.0
    both_have = a_has[:, None] & b_has[None, :]
    if both_have.any():
        conn_text_sim = _text_similarity(a_conn, b_conn)
        conn[both_have] = conn_text_sim[both_have]

    return 0.5 * cp + 0.5 * conn


def hybrid_similarity_matrix(a_df: pd.DataFrame, b_df: pd.DataFrame | None = None) -> np.ndarray:
    """Итоговая гибридная матрица сходства A x B (B = A, если не передан):

    0.45 * текст назначения + 0.25 * числовые признаки + 0.30 * контрагенты/связи.
    """
    if b_df is None:
        b_df = a_df
    sim = (
        SIMILARITY_TEXT_WEIGHT * _text_similarity(a_df["purpose"], b_df["purpose"])
        + SIMILARITY_NUMERIC_WEIGHT * _SPACE.numeric_similarity(a_df, b_df)
        + SIMILARITY_CONTEXT_WEIGHT * _context_similarity(a_df, b_df)
    )
    return np.clip(sim, 0.0, 1.0)


def cluster_density_profile(sim_matrix: np.ndarray) -> dict[str, Any]:
    """Профиль компактности кластера по попарной матрице сходства:

    - homogeneity: средняя попарная похожесть (внедиагональная);
    - isolated_share: доля операций, у которых даже наиболее похожая операция
      кластера ниже порога уверенности распространения.
    """
    n = len(sim_matrix)
    if n <= 1:
        return {"n_operations": n, "homogeneity": 1.0, "isolated_share": 0.0}

    mask = ~np.eye(n, dtype=bool)
    homogeneity = float(sim_matrix[mask].mean())
    masked = np.where(mask, sim_matrix, -np.inf)
    best_neighbor = masked.max(axis=1)
    isolated_share = float((best_neighbor < PROPAGATION_CONFIDENCE_LOW).mean())
    return {"n_operations": n, "homogeneity": homogeneity, "isolated_share": isolated_share}


def classify_cluster_density(profile: dict[str, Any], *, homogeneity_threshold: float | None, max_isolated_share: float) -> str:
    """Кластер разреженный, если однородность ниже порога либо изолированных операций много."""
    threshold = homogeneity_threshold if homogeneity_threshold is not None else PROPAGATION_CONFIDENCE_LOW
    if profile["homogeneity"] < threshold or profile["isolated_share"] > max_isolated_share:
        return "sparse"
    return "compact"


def adaptive_select_representatives(sim_matrix: np.ndarray, coverage_threshold: float, k_max: int) -> list[int]:
    """Farthest Point Sampling с критерием остановки по покрытию.

    FPS — жадная 2-аппроксимация задачи k-center (минимизировать максимальное
    расстояние от операции до ближайшего представителя). Старт — с самой
    атипичной операции (минимальная средняя похожесть), выбор продолжается,
    пока худшая операция не покрыта представителем со сходством >= порога
    (или не достигнут k_max).
    """
    n = len(sim_matrix)
    if n == 0:
        return []
    if n == 1:
        return [0]

    selected = [int(np.argmin(sim_matrix.mean(axis=1)))]
    k_max = max(1, min(k_max, n))

    while len(selected) < k_max:
        coverage = sim_matrix[:, selected].max(axis=1)
        coverage[selected] = 1.0
        worst = int(np.argmin(coverage))
        if coverage[worst] >= coverage_threshold:
            break
        selected.append(worst)

    return selected
