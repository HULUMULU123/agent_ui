from __future__ import annotations

import logging
from typing import Any, Callable

import pandas as pd

from .clustering_strategy import assign_batch_groups
from .config import MAX_OPERATIONS_PER_LLM_BATCH, PROPAGATION_CONFIDENCE_HIGH, PROPAGATION_CONFIDENCE_LOW
from .similarity import hybrid_similarity_matrix, resolve_counterparty_inn

logger = logging.getLogger("bankruptcy_agent.propagation")

# Портировано из notebooks/agent_v3_adaptive_clusters_1.ipynb:
# "Распространение результатов анализа на весь кластер" (ячейка 39) и
# "Повторный LLM-анализ операций с низкой уверенностью" (ячейка 41).

PROPAGATED_ANALYSIS_FIELDS = [
    "risk_level",
    "transaction_category",
    "counterparty_category",
    "legal_qualification",
    "legal_basis",
    "court_basis",
    "decision_argumentation",
    "risk_explanation",
    "recommendation",
    "challenge_criteria",
    "connections_basis",
    "used_tools",
    "cluster_summary",
    "overall_risk_assessment",
]


def _confidence_label(similarity: float) -> str:
    if similarity >= PROPAGATION_CONFIDENCE_HIGH:
        return "high"
    if similarity >= PROPAGATION_CONFIDENCE_LOW:
        return "medium"
    return "low"


def _analysis_row(op: pd.Series, analysis: dict[str, Any] | pd.Series, **tech_fields: Any) -> dict[str, Any]:
    row = {**op.to_dict()}
    for field in PROPAGATED_ANALYSIS_FIELDS:
        if field in analysis:
            row[field] = analysis[field]
    row.update(tech_fields)
    return row


def propagate_cluster_analysis(
    full_df: pd.DataFrame,
    analyzed_df: pd.DataFrame,
    cluster_strategy: dict[Any, dict[str, Any]],
) -> pd.DataFrame:
    """Заполняет полную выписку результатами анализа.

    Для каждой операции:
    - проанализирована LLM напрямую (представитель или операция разреженного
      кластера) -> результат берется как есть, analysis_source = "llm";
    - компактный кластер -> k-NN (k=1) к представителям кластера по гибридной
      метрике, перенос анализа + confidence;
    - разреженный кластер, операция не анализировалась (ниже порога суммы) ->
      распространение запрещено по построению стратегии, операция помечается;
    - шумовой кластер / кластер без представителей -> помечается на проверку.
    """
    if analyzed_df is None or analyzed_df.empty:
        result = full_df.copy()
        result["analysis_source"] = "none"
        result["matched_representative_idx"] = None
        result["similarity_score"] = None
        result["propagation_confidence"] = None
        result["propagation_status"] = "no_analysis"
        result["needs_review"] = True
        return result

    analyzed_map: dict[str, dict[str, Any]] = {
        str(row["idx"]): row for _, row in analyzed_df.iterrows() if pd.notna(row.get("idx"))
    }
    logger.info("Распространение: операций с готовым LLM-анализом: %d", len(analyzed_map))

    out_rows: list[dict[str, Any]] = []

    for cluster_id, ops in full_df.groupby("cluster", sort=False):
        ops = ops.copy()
        strategy = cluster_strategy.get(cluster_id, {})
        mode = strategy.get("mode")

        analyzed_mask = ops["idx"].astype(str).isin(analyzed_map)
        analyzed_ops = ops[analyzed_mask]

        for _, op in analyzed_ops.iterrows():
            analysis = analyzed_map[str(op["idx"])]
            out_rows.append(_analysis_row(
                op, analysis,
                analysis_source="llm",
                matched_representative_idx=op["idx"],
                similarity_score=1.0,
                propagation_confidence="high",
                propagation_status="analyzed_by_llm",
                needs_review=False,
            ))

        rest = ops[~analyzed_mask]
        if rest.empty:
            continue

        if cluster_id == -1 or analyzed_ops.empty:
            status = "noise_cluster" if cluster_id == -1 else "no_representative"
            for _, op in rest.iterrows():
                out_rows.append({
                    **op.to_dict(),
                    "analysis_source": "none",
                    "matched_representative_idx": None,
                    "similarity_score": None,
                    "propagation_confidence": None,
                    "propagation_status": status,
                    "needs_review": True,
                })
            continue

        if mode == "sparse":
            for _, op in rest.iterrows():
                out_rows.append({
                    **op.to_dict(),
                    "analysis_source": "none",
                    "matched_representative_idx": None,
                    "similarity_score": None,
                    "propagation_confidence": None,
                    "propagation_status": "sparse_below_threshold",
                    "needs_review": True,
                })
            continue

        combined = hybrid_similarity_matrix(rest, analyzed_ops)
        best_pos = combined.argmax(axis=1)
        best_sim = combined.max(axis=1)
        rep_records = analyzed_ops.to_dict(orient="records")

        for (_, op), pos, sim in zip(rest.iterrows(), best_pos, best_sim):
            rep_op = rep_records[int(pos)]
            rep_idx = str(rep_op["idx"])
            analysis = analyzed_map[rep_idx]
            confidence = _confidence_label(float(sim))

            same_cp = resolve_counterparty_inn(op) == resolve_counterparty_inn(pd.Series(rep_op))
            try:
                rep_risk = int(float(analysis.get("risk_level")))
            except (TypeError, ValueError):
                rep_risk = None
            if not same_cp and rep_risk is not None and rep_risk >= 2 and confidence == "high":
                confidence = "medium"

            out_rows.append(_analysis_row(
                op, analysis,
                analysis_source="propagated",
                matched_representative_idx=rep_idx,
                similarity_score=round(float(sim), 4),
                propagation_confidence=confidence,
                propagation_status="propagated",
                propagation_note=(
                    f"Анализ перенесен с наиболее похожей операции-представителя idx={rep_idx} "
                    f"(сходство {float(sim):.2f}). Аргументация описывает представителя и требует "
                    "сверки с деталями текущей операции."
                ),
                needs_review=confidence == "low",
            ))

    result = pd.DataFrame(out_rows)
    logger.info(
        "Распространение завершено: всего=%d, llm=%d, propagated=%d, без анализа=%d, низкая уверенность=%d",
        len(result),
        int((result["analysis_source"] == "llm").sum()) if not result.empty else 0,
        int((result["analysis_source"] == "propagated").sum()) if not result.empty else 0,
        int((result["analysis_source"] == "none").sum()) if not result.empty else 0,
        int((result["propagation_confidence"] == "low").sum()) if not result.empty else 0,
    )
    return result


def select_second_pass_operations(full_analysis: pd.DataFrame) -> pd.DataFrame:
    """Операции с низкой уверенностью пропагации, годные для повторного LLM-анализа.

    Операция с confidence = low не получает объяснение ближайшего представителя:
    она автоматически уходит на второй проход. Операции ниже порога материальности
    остаются помеченными на ручную проверку (фильтр стоимости не обходится вторым проходом).
    """
    if full_analysis.empty:
        return full_analysis.copy()
    needs_review = full_analysis.get("needs_review")
    analysis_source = full_analysis.get("analysis_source")
    below_threshold = full_analysis.get("is_below_llm_threshold")
    if needs_review is None or analysis_source is None:
        return full_analysis.iloc[0:0].copy()
    below_threshold = below_threshold if below_threshold is not None else pd.Series(False, index=full_analysis.index)
    mask = (needs_review == True) & (analysis_source == "propagated") & (~below_threshold.fillna(False))  # noqa: E712
    return full_analysis[mask].copy()


def run_second_pass(
    full_analysis: pd.DataFrame,
    analyze_fn: Callable[[pd.DataFrame], pd.DataFrame | None],
    *,
    max_operations_per_llm_batch: int = MAX_OPERATIONS_PER_LLM_BATCH,
) -> tuple[pd.DataFrame, int]:
    """Повторно анализирует операции с низкой уверенностью и мержит результат обратно.

    `analyze_fn` — тот же путь анализа, что и для первого прохода (реальный
    агент или детерминированный fallback), принимает DataFrame с колонкой
    `batch_group` и возвращает DataFrame с колонкой `idx` в контракте
    AnalyzerClusterResult.operations.
    """
    second_pass_ops = select_second_pass_operations(full_analysis)
    logger.info("Операций на повторный LLM-анализ: %d", len(second_pass_ops))
    if second_pass_ops.empty:
        return full_analysis, 0

    batched = assign_batch_groups(second_pass_ops, max_operations_per_llm_batch=max_operations_per_llm_batch)
    analyzed = analyze_fn(batched)
    if analyzed is None or analyzed.empty or "idx" not in analyzed.columns:
        logger.warning("Второй проход не вернул результатов; операции остаются в needs_review.")
        return full_analysis, 0

    second_pass_map = analyzed.copy()
    second_pass_map["idx"] = second_pass_map["idx"].astype(str)
    second_pass_map = second_pass_map.drop_duplicates(subset="idx", keep="last").set_index("idx")

    result = full_analysis.copy()
    for field in PROPAGATED_ANALYSIS_FIELDS:
        if field not in result.columns:
            result[field] = None

    result["idx_str"] = result["idx"].astype(str)
    updated = 0
    target_positions = result.index[result["idx_str"].isin(second_pass_map.index) & result.index.isin(second_pass_ops.index)]
    for pos in target_positions:
        op_idx = result.at[pos, "idx_str"]
        analysis = second_pass_map.loc[op_idx]
        for field in PROPAGATED_ANALYSIS_FIELDS:
            if field in analysis.index:
                result.at[pos, field] = analysis[field]
        result.at[pos, "analysis_source"] = "llm_second_pass"
        result.at[pos, "propagation_status"] = "reanalyzed_by_llm"
        result.at[pos, "propagation_confidence"] = "high"
        result.at[pos, "similarity_score"] = 1.0
        result.at[pos, "matched_representative_idx"] = op_idx
        result.at[pos, "needs_review"] = False
        updated += 1

    result = result.drop(columns=["idx_str"])
    logger.info("Второй проход: обновлено операций=%d из %d", updated, len(second_pass_ops))
    return result, updated
