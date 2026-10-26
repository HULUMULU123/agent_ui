"""Гибридная метрика сходства операций, кластеры, выбор представителей (FPS) и локальная кластеризация."""
from __future__ import annotations

import logging
import math
from typing import Any

import numpy as np
import pandas as pd
from sklearn.cluster import AgglomerativeClustering
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity, euclidean_distances

from .common import AFFILIATION_COLUMNS, AgentConfig, is_missing, resolve_counterparty_inn

logger = logging.getLogger("finapp.agent.similarity")

# 0.40 — текст назначения (TF-IDF символьных 3–5-грамм), 0.20 — числа, 0.25 — контрагент и связи, 0.15 — тип операции.
SIMILARITY_WEIGHTS = {"text": 0.40, "numeric": 0.20, "context": 0.25, "type": 0.15}
PROPAGATION_CONFIDENCE_HIGH = 0.85
PROPAGATION_CONFIDENCE_LOW = 0.65


def _text_similarity(a: pd.Series, b: pd.Series) -> np.ndarray:
    a_list = a.fillna("").astype(str).str.lower().tolist()
    b_list = b.fillna("").astype(str).str.lower().tolist()
    # use_idf=False: на малых выборках IDF обнуляет общие n-граммы шаблонных назначений.
    vectorizer = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), use_idf=False)
    try:
        matrix = vectorizer.fit_transform(a_list + b_list)
    except ValueError:
        return np.full((len(a_list), len(b_list)), 0.5)
    return cosine_similarity(matrix[:len(a_list)], matrix[len(a_list):])


def _numeric_block(frame: pd.DataFrame) -> pd.DataFrame:
    block = pd.DataFrame(index=frame.index)
    for col in ("anomaly_score", "anomaly_model_score", "interval_days"):
        block[col] = pd.to_numeric(frame[col], errors="coerce") if col in frame.columns else np.nan
    block["amount_log"] = np.log1p(pd.to_numeric(frame["amount"], errors="coerce").abs())
    return block


def _relations_text(frame: pd.DataFrame) -> pd.Series:
    col = next((c for c in AFFILIATION_COLUMNS if c in frame.columns), None)
    if col is None:
        return pd.Series("", index=frame.index)
    return frame[col].map(lambda v: "" if is_missing(v) else (" ; ".join(map(str, v)) if isinstance(v, (list, tuple)) else str(v)))


class SimilaritySpace:
    """Нормализация числовых признаков по всей выписке (квантили 1%/99%), общая для всех сравнений."""

    def __init__(self, reference: pd.DataFrame, cfg: AgentConfig):
        self.cfg = cfg
        self.bounds: dict[str, tuple[float, float]] = {}
        block = _numeric_block(reference)
        for col in block.columns:
            low, high = block[col].quantile(0.01), block[col].quantile(0.99)
            self.bounds[col] = (0.0, 1.0) if pd.isna(low) or pd.isna(high) or high <= low else (float(low), float(high))

    def _numeric_similarity(self, a: pd.DataFrame, b: pd.DataFrame) -> np.ndarray:
        def scaled(frame: pd.DataFrame) -> np.ndarray:
            block = _numeric_block(frame)
            out = pd.DataFrame({col: ((block[col] - lo) / (hi - lo)).clip(0, 1) for col, (lo, hi) in self.bounds.items()})
            return out.fillna(out.median()).fillna(0.5).to_numpy(dtype=float)
        x_a, x_b = scaled(a), scaled(b)
        # евклидово расстояние без 3-мерного массива n×m×k (при 4000 операций он занимал бы ~0,5 ГБ)
        return 1.0 - euclidean_distances(x_a, x_b) / math.sqrt(x_a.shape[1])

    @staticmethod
    def _context_similarity(a: pd.DataFrame, b: pd.DataFrame) -> np.ndarray:
        """Половина — совпадение контрагента (0.5, если не определён), половина — совпадение картины связей."""
        a_cp = np.array([resolve_counterparty_inn(r) for r in a.to_dict("records")], dtype=object)
        b_cp = np.array([resolve_counterparty_inn(r) for r in b.to_dict("records")], dtype=object)
        cp = (a_cp[:, None] == b_cp[None, :]).astype(float)
        cp[pd.isna(a_cp), :] = 0.5
        cp[:, pd.isna(b_cp)] = 0.5

        a_rel, b_rel = _relations_text(a), _relations_text(b)
        a_has, b_has = a_rel.str.strip().astype(bool).to_numpy(), b_rel.str.strip().astype(bool).to_numpy()
        rel = np.zeros((len(a), len(b)))
        rel[(~a_has[:, None]) & (~b_has[None, :])] = 1.0
        both = a_has[:, None] & b_has[None, :]
        if both.any():
            rel[both] = _text_similarity(a_rel, b_rel)[both]
        return 0.5 * cp + 0.5 * rel

    @staticmethod
    def _type_similarity(a: pd.DataFrame, b: pd.DataFrame) -> np.ndarray:
        def settled(frame: pd.DataFrame) -> np.ndarray:
            col = frame["operation_type"] if "operation_type" in frame.columns else pd.Series(index=frame.index, dtype=object)
            return np.array([t if isinstance(t, str) and t and not t.startswith("Неопознанный") else None for t in col], dtype=object)
        a_t, b_t = settled(a), settled(b)
        sim = (a_t[:, None] == b_t[None, :]).astype(float)
        sim[pd.isna(a_t), :] = 0.5
        sim[:, pd.isna(b_t)] = 0.5
        return sim

    def matrix(self, a: pd.DataFrame, b: pd.DataFrame | None = None) -> np.ndarray:
        b = a if b is None else b
        sim = (SIMILARITY_WEIGHTS["text"] * _text_similarity(a["purpose"], b["purpose"])
               + SIMILARITY_WEIGHTS["numeric"] * self._numeric_similarity(a, b)
               + SIMILARITY_WEIGHTS["context"] * self._context_similarity(a, b)
               + SIMILARITY_WEIGHTS["type"] * self._type_similarity(a, b))
        return np.clip(sim, 0.0, 1.0)

    # ── кластеры ──

    @staticmethod
    def density_profile(sim: np.ndarray) -> dict:
        n = len(sim)
        if n <= 1:
            return {"n_operations": n, "homogeneity": 1.0, "isolated_share": 0.0}
        off_diagonal = ~np.eye(n, dtype=bool)
        best_neighbor = np.where(off_diagonal, sim, -np.inf).max(axis=1)
        return {"n_operations": n, "homogeneity": float(sim[off_diagonal].mean()),
                "isolated_share": float((best_neighbor < PROPAGATION_CONFIDENCE_LOW).mean())}

    def classify_density(self, profile: dict) -> str:
        threshold = self.cfg.cluster_homogeneity_threshold or PROPAGATION_CONFIDENCE_LOW
        sparse = profile["homogeneity"] < threshold or profile["isolated_share"] > self.cfg.max_isolated_share
        return "sparse" if sparse else "compact"

    @staticmethod
    def select_representatives(sim: np.ndarray, coverage_threshold: float, k_max: int) -> list[int]:
        """FPS со стартом с самой нетипичной операции, пока худшее покрытие < порога или не достигнут k_max."""
        n = len(sim)
        if n <= 1:
            return list(range(n))
        selected = [int(np.argmin(sim.mean(axis=1)))]
        while len(selected) < min(k_max, n):
            coverage = sim[:, selected].max(axis=1)
            coverage[selected] = 1.0
            worst = int(np.argmin(coverage))
            if coverage[worst] >= coverage_threshold:
                break
            selected.append(worst)
        return selected

    def assign_local_clusters(self, frame: pd.DataFrame) -> pd.DataFrame:
        """Кластеры, когда в выписке их нет: complete linkage по гибридной метрике (порог 0.85).

        До порога no_cluster_local_clustering_max_ops — по всей выписке сразу (как в ноутбуке). Для больших выписок матрица n×n
        не помещается в память, поэтому операции сначала делятся на группы по очищенному назначению и направлению (шаблонные
        платежи схлопываются), а кластеризация идёт внутри групп, не превышающих порог; крупные группы режутся на части.
        """
        frame = frame.copy()
        if len(frame) < 2:
            frame["cluster"] = -1
            return frame
        if len(frame) <= self.cfg.no_cluster_local_clustering_max_ops:
            frame["cluster"] = self._cluster_block(frame)
            return frame
        limit = self.cfg.no_cluster_local_clustering_max_ops
        labels = pd.Series(-1, index=frame.index, dtype="int64")
        next_label = 0
        keys = frame["purpose_cleaned"].fillna("").astype(str) + "|" + frame["direction"].astype(str)
        for _, group_index in frame.groupby(keys, sort=False).groups.items():
            group = frame.loc[group_index]
            if len(group) < 2:
                continue
            ordered = group.assign(_cp=[resolve_counterparty_inn(r) or "" for r in group.to_dict("records")]) \
                .sort_values(["_cp", "amount"], kind="mergesort").drop(columns="_cp")
            for start in range(0, len(ordered), limit):
                part = ordered.iloc[start:start + limit]
                if len(part) < 2:
                    continue
                part_labels = self._cluster_block(part)
                mapped = part_labels.where(part_labels == -1, part_labels + next_label)
                labels.loc[part.index] = mapped.to_numpy()
                if (part_labels >= 0).any():
                    next_label += int(part_labels.max()) + 1
        frame["cluster"] = labels
        logger.info("Локальная кластеризация по группам назначений: операций %d, групп %d", len(frame), frame["cluster"].nunique())
        return frame

    def _cluster_block(self, block: pd.DataFrame) -> pd.Series:
        threshold = self.cfg.no_cluster_similarity_threshold or PROPAGATION_CONFIDENCE_HIGH
        distance = np.clip(1.0 - self.matrix(block), 0.0, None)
        np.fill_diagonal(distance, 0.0)
        raw = AgglomerativeClustering(metric="precomputed", linkage="complete",
                                      distance_threshold=1.0 - threshold, n_clusters=None).fit_predict(distance)
        sizes = pd.Series(raw).value_counts()
        return pd.Series([-1 if sizes[label] == 1 else int(label) for label in raw], index=block.index, dtype="int64")


def select_for_llm(df_sample: pd.DataFrame, space: SimilaritySpace, cfg: AgentConfig) -> tuple[pd.DataFrame, dict[Any, dict]]:
    """Кластеры -> операции для первого прохода LLM + диагностика плотности по кластерам."""
    cluster_strategy: dict[Any, dict] = {}
    selected_parts: list[pd.DataFrame] = []
    for cluster_id, group in df_sample.groupby("cluster", sort=False, dropna=False):
        if cluster_id == -1 or len(group) == 1:
            mode, chosen = "sparse", group
            profile = {"n_operations": len(group), "homogeneity": np.nan, "isolated_share": np.nan}
        else:
            sim = space.matrix(group)
            profile = space.density_profile(sim)
            mode = space.classify_density(profile)
            chosen = group if mode == "sparse" else group.iloc[space.select_representatives(
                sim, PROPAGATION_CONFIDENCE_LOW, cfg.max_representatives_per_cluster)]
        skipped = mode == "sparse" and cluster_id != -1 and not cfg.analyze_sparse_clusters
        cluster_strategy[cluster_id] = {"mode": mode, "skipped": skipped, "n_selected": 0 if skipped else len(chosen),
                                        **{k: round(v, 4) if isinstance(v, float) else v for k, v in profile.items()}}
        if not skipped:
            selected_parts.append(chosen)
    selected = pd.concat(selected_parts) if selected_parts else df_sample.iloc[0:0]
    return selected, cluster_strategy
