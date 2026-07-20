from __future__ import annotations

import logging
import math
from typing import Any

import pandas as pd

from .config import (
    ANALYZE_SPARSE_CLUSTERS,
    CLUSTER_HOMOGENEITY_THRESHOLD,
    MAX_ISOLATED_SHARE,
    MAX_OPERATIONS_PER_LLM_BATCH,
    MAX_REPRESENTATIVES_PER_CLUSTER,
)
from .similarity import (
    adaptive_select_representatives,
    classify_cluster_density,
    cluster_density_profile,
    hybrid_similarity_matrix,
)

logger = logging.getLogger("bankruptcy_agent.clustering_strategy")

# Портировано из notebooks/agent_v3_adaptive_clusters_1.ipynb:
# "Применение адаптивной стратегии к кластерам" + батчинг разреженных
# кластеров (ячейки 30-35, 37).


def apply_cluster_strategy(
    df_sample: pd.DataFrame,
    *,
    homogeneity_threshold: float | None = CLUSTER_HOMOGENEITY_THRESHOLD,
    max_isolated_share: float = MAX_ISOLATED_SHARE,
    max_representatives_per_cluster: int = MAX_REPRESENTATIVES_PER_CLUSTER,
    max_operations_per_llm_batch: int = MAX_OPERATIONS_PER_LLM_BATCH,
    analyze_sparse_clusters: bool = ANALYZE_SPARSE_CLUSTERS,
) -> tuple[pd.DataFrame, dict[Any, dict[str, Any]], pd.DataFrame]:
    """Строит LLM-выборку по адаптивной стратегии плотности кластеров.

    `df_sample` — операции, уже отфильтрованные по порогу суммы
    (`amount.abs() >= min_abs_amount_for_llm`), с колонкой `cluster`.

    Возвращает:
    - `df_unique` — представители компактных кластеров + разреженные кластеры
      целиком, с колонками `selection_mode` и `batch_group`;
    - `cluster_strategy` — словарь диагностики по каждому кластеру;
    - `diagnostics_df` — тот же словарь в виде DataFrame (для экспорта).
    """
    df_sample = df_sample.reset_index(drop=True)
    cluster_strategy: dict[Any, dict[str, Any]] = {}
    selected_parts: list[pd.DataFrame] = []

    for cluster_id, group in df_sample.groupby("cluster", sort=False):
        if cluster_id == -1:
            # Шумовой кластер не имеет смысловой общности: стратегия не
            # применяется, операции исключаются из LLM-выборки.
            continue

        sim = hybrid_similarity_matrix(group)
        profile = cluster_density_profile(sim)
        mode = classify_cluster_density(
            profile,
            homogeneity_threshold=homogeneity_threshold,
            max_isolated_share=max_isolated_share,
        )

        if mode == "sparse" and not analyze_sparse_clusters:
            # Разреженный кластер исключён из LLM-анализа по конфигу: операции не
            # передаются агенту и позже помечаются на ручную проверку.
            cluster_strategy[cluster_id] = {
                "cluster": cluster_id,
                "mode": mode,
                "skipped": True,
                "n_operations_llm_eligible": profile["n_operations"],
                "homogeneity": round(profile["homogeneity"], 4),
                "isolated_share": round(profile["isolated_share"], 4),
                "n_selected": 0,
                "min_coverage_after_selection": 0.0,
            }
            continue

        if mode == "sparse":
            chosen = group.copy()
            chosen["selection_mode"] = "full_sparse"
            min_coverage = 1.0
        else:
            idx_positions = adaptive_select_representatives(
                sim,
                coverage_threshold=CLUSTER_HOMOGENEITY_THRESHOLD or 0.65,
                k_max=max_representatives_per_cluster,
            )
            chosen = group.iloc[idx_positions].copy()
            chosen["selection_mode"] = "representative"
            min_coverage = float(sim[:, idx_positions].max(axis=1).min())

        chosen["batch_group"] = _batch_group_labels(cluster_id, len(chosen), mode, max_operations_per_llm_batch)

        cluster_strategy[cluster_id] = {
            "cluster": cluster_id,
            "mode": mode,
            "skipped": False,
            "n_operations_llm_eligible": profile["n_operations"],
            "homogeneity": round(profile["homogeneity"], 4),
            "isolated_share": round(profile["isolated_share"], 4),
            "n_selected": len(chosen),
            "min_coverage_after_selection": round(min_coverage, 4),
        }
        selected_parts.append(chosen)

    df_unique = pd.concat(selected_parts, ignore_index=True) if selected_parts else df_sample.iloc[0:0].copy()
    if "selection_mode" not in df_unique.columns:
        df_unique["selection_mode"] = pd.Series(dtype=str)
    if "batch_group" not in df_unique.columns:
        df_unique["batch_group"] = pd.Series(dtype=str)

    diagnostics_df = (
        pd.DataFrame.from_dict(cluster_strategy, orient="index")
        .rename_axis("cluster_key")
        .reset_index(drop=True)
        .sort_values("homogeneity")
        if cluster_strategy
        else pd.DataFrame(columns=["cluster", "mode", "n_operations_llm_eligible", "homogeneity", "isolated_share", "n_selected", "min_coverage_after_selection"])
    )

    logger.info(
        "Стратегия кластеров: всего=%d, компактных=%d, разреженных=%d; операций в LLM: %d из %d релевантных",
        len(cluster_strategy),
        sum(1 for s in cluster_strategy.values() if s["mode"] == "compact"),
        sum(1 for s in cluster_strategy.values() if s["mode"] == "sparse"),
        len(df_unique),
        len(df_sample),
    )
    return df_unique, cluster_strategy, diagnostics_df


def _batch_group_labels(cluster_id: Any, n: int, mode: str, max_batch: int) -> list[str]:
    """Для sparse-кластеров крупнее max_batch — суффикс _partN, иначе один батч."""
    if mode == "sparse" and n > max_batch:
        n_parts = math.ceil(n / max_batch)
        labels = []
        for i in range(n):
            part = i // max_batch + 1
            labels.append(f"{cluster_id}_part{min(part, n_parts)}")
        return labels
    return [str(cluster_id)] * n


def assign_batch_groups(df: pd.DataFrame, *, max_operations_per_llm_batch: int = MAX_OPERATIONS_PER_LLM_BATCH) -> pd.DataFrame:
    """Пересобирает batch_group для произвольного набора операций (например,
    для второго LLM-прохода по операциям с низкой уверенностью), группируя по
    исходному cluster и нарезая крупные группы на части.
    """
    if df.empty:
        result = df.copy()
        result["batch_group"] = pd.Series(dtype=str)
        return result

    result = df.reset_index(drop=True).copy()
    cluster_col = result["cluster"] if "cluster" in result.columns else pd.Series([0] * len(result))
    labels: list[str] = [""] * len(result)
    for cluster_id, group in result.groupby(cluster_col, sort=False):
        n = len(group)
        n_parts = math.ceil(n / max_operations_per_llm_batch)
        for local_pos, orig_pos in enumerate(group.index):
            if n_parts <= 1:
                labels[orig_pos] = f"{cluster_id}_second_pass"
            else:
                part = local_pos // max_operations_per_llm_batch + 1
                labels[orig_pos] = f"{cluster_id}_second_pass_part{part}"
    result["batch_group"] = labels
    return result
