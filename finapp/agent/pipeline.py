"""Прогон анализа: таблица операций -> типы -> кластеры -> первый проход -> перенос -> повторные проходы -> память риска.

Последовательность и пороги соответствуют ноутбуку agent_v4_gc3_no_chains. Гарантия покрытия: каждая операция
получает прямой анализ, перенос с похожей операции, результат повторного прохода либо явную пометку
«нужна ручная проверка».
"""
from __future__ import annotations

import logging
import math
import threading
from dataclasses import dataclass, field
from typing import Any, Callable

import numpy as np
import pandas as pd

from .classifier import classify_operation_types
from .common import DataProfile, SanitizeCounter, resolve_counterparty_inn, to_risk_level
from .dataprep import PreparedData, prepare_statement, rule_skip_mask
from .fallback import big_amount_threshold, rule_based_analysis
from .graph import BatchAnalysis, llm_identity_key
from .prompts import ROUTES
from .risk_memory import update_risk_memory_from_results
from .runtime import Runtime, run_batches_parallel
from .similarity import (PROPAGATION_CONFIDENCE_HIGH, PROPAGATION_CONFIDENCE_LOW, SimilaritySpace, select_for_llm)

logger = logging.getLogger("finapp.agent.pipeline")

ProgressFn = Callable[[float, str], None]

ANALYSIS_FIELDS = [
    "risk_level", "risk_factors", "check_route", "counterparty_category", "legal_qualification", "legal_basis",
    "court_basis", "decision_argumentation", "risk_explanation", "recommendation", "challenge_criteria",
    "connections_basis", "documents_to_request", "verification_goal", "used_tools",
]
EMPTY_ANALYSIS: dict[str, Any] = {f: None for f in ANALYSIS_FIELDS}


class AnalysisCancelled(Exception):
    """Анализ остановлен пользователем."""


def _rule_result(years: int | None) -> dict:
    return {
        "risk_level": 0, "risk_factors": [], "check_route": "none", "counterparty_category": None,
        "legal_qualification": ROUTES["none"][0], "legal_basis": [], "court_basis": [],
        "decision_argumentation": (f"Операция совершена более чем за {years} года до подачи заявления о банкротстве — "
                                   "за пределами периодов подозрительности (ст. 61.2, 61.3 Закона о банкротстве). "
                                   "Автоматический анализ не проводился."),
        "risk_explanation": "Вне периодов подозрительности.",
        "recommendation": "Дополнительные действия не требуются.", "challenge_criteria": "",
        "connections_basis": "", "documents_to_request": [], "verification_goal": "", "used_tools": [],
    }


def _outcome(analysis: dict, source: str, representative: str | None, similarity: float | None,
             confidence: str | None, needs_review: bool) -> dict:
    return {**{f: analysis.get(f) for f in ANALYSIS_FIELDS}, "analysis_source": source,
            "matched_representative_idx": representative, "similarity_score": similarity,
            "propagation_confidence": confidence, "needs_review": needs_review}


def _has_valid_risk(analysis: dict) -> bool:
    """Guard проверяет только присутствие idx в ответе, а не заполненность risk_level: без этой проверки операция
    с забытым risk_level считалась бы проанализированной и не ушла бы в повторный проход."""
    return to_risk_level(analysis.get("risk_level")) is not None


@dataclass
class AnalysisResult:
    full_analysis: pd.DataFrame
    rejected: pd.DataFrame
    notes: list[str]
    stats: dict[str, Any] = field(default_factory=dict)
    cluster_strategy: dict = field(default_factory=dict)
    tokens: dict = field(default_factory=dict)
    memory_update: dict = field(default_factory=dict)
    profile: DataProfile = field(default_factory=DataProfile)


class AnalysisPipeline:
    def __init__(self, rt: Runtime, progress: ProgressFn | None = None, cancel: threading.Event | None = None):
        self.rt = rt
        self.cfg = rt.cfg
        self._progress = progress or (lambda fraction, message: None)
        self.cancel = cancel or threading.Event()

    # ── вспомогательное ──

    def report(self, fraction: float, message: str) -> None:
        self._check_cancel()
        self._progress(max(0.0, min(1.0, fraction)), message)

    def _check_cancel(self) -> None:
        if self.cancel.is_set():
            raise AnalysisCancelled("Анализ остановлен пользователем")

    def _stage_progress(self, start: float, end: float, label: str) -> Callable[[int, int], None]:
        def on_done(done: int, total: int) -> None:
            self._progress(start + (end - start) * done / max(total, 1), f"{label}: {done} из {total}")
        return on_done

    # ── пакеты ──

    @staticmethod
    def build_batches(frame: pd.DataFrame, max_batch: int) -> list[tuple[str, list[dict]]]:
        groups = sorted(frame.groupby("cluster", sort=False, dropna=False),
                        key=lambda item: str(item[1]["purpose_cleaned"].iloc[0]))
        batches: list[tuple[str, list[dict]]] = []
        buffer: list[dict] = []
        labels: list[str] = []

        def flush() -> None:
            if buffer:
                batches.append(("+".join(labels), list(buffer)))
                buffer.clear()
                labels.clear()

        for cluster_id, group in groups:
            records = group.sort_values(["purpose_cleaned", "amount"], kind="mergesort").to_dict("records")
            if len(records) > max_batch:
                flush()
                for part in range(math.ceil(len(records) / max_batch)):
                    batches.append((f"{cluster_id}_part{part + 1}", records[part * max_batch:(part + 1) * max_batch]))
                continue
            if len(buffer) + len(records) > max_batch:
                flush()
            buffer.extend(records)
            labels.append(str(cluster_id))
        flush()
        return batches

    def run_batches(self, frame: pd.DataFrame, analysis: BatchAnalysis, profile: DataProfile, desc: str,
                    start: float, end: float) -> dict[str, dict]:
        """Анализ операций frame; одинаковые для модели операции уходят один раз. -> {idx: результат}."""
        if frame.empty:
            return {}
        keys = frame.apply(lambda r: llm_identity_key(r.to_dict(), profile), axis=1)
        unique = frame[~keys.duplicated()]
        if len(unique) < len(frame):
            logger.info("%s: %d уникальных операций из %d.", desc, len(unique), len(frame))
        results: dict[str, dict] = {}

        def worker(batch: tuple[str, list[dict]]) -> dict:
            if self.cancel.is_set():
                return {}
            return analysis.run(*batch)

        for item in run_batches_parallel(self.build_batches(unique, self.cfg.max_operations_per_llm_batch), worker, desc,
                                         self.cfg.llm_max_workers,
                                         on_done=self._stage_progress(start, end, desc)):
            results.update((item or {}).get("results", {}))
        self._check_cancel()
        key_by_idx = dict(zip(frame["idx"].astype(str), keys))
        result_by_key = {key_by_idx[idx]: res for idx, res in results.items() if idx in key_by_idx}
        return {idx: result_by_key[key] for idx, key in key_by_idx.items() if key in result_by_key}

    # ── основной прогон ──

    def run(self, raw: pd.DataFrame, spark_df: pd.DataFrame | None = None, *, disable_relations: bool = False,
            disable_clustering: bool = False) -> AnalysisResult:
        rt, cfg = self.rt, self.cfg
        SanitizeCounter.count = 0
        self.report(0.01, "Проверяю и нормализую таблицу операций")
        prepared: PreparedData = prepare_statement(raw, cfg, spark_df, disable_relations=disable_relations,
                                                   disable_clustering=disable_clustering)
        df, profile = prepared.df, prepared.profile
        notes = list(prepared.notes)

        self.report(0.06, "Определяю типы операций" if rt.use_llm else "Определяю типы операций по формулировкам")
        types = classify_operation_types(rt, df, on_done=self._stage_progress(0.06, 0.20, "Определение типов операций"))
        df = df.merge(types, on="idx", how="left", validate="one_to_one")
        self._check_cancel()

        rule_mask = rule_skip_mask(df, cfg)
        space = SimilaritySpace(df, cfg)
        if not profile.clustering_available and cfg.no_cluster_local_clustering:
            self.report(0.21, "Группирую похожие операции")
            skipped_idx = set(df.loc[rule_mask, "idx"])
            df = space.assign_local_clusters(df)
            profile.local_clustering_done = bool((df["cluster"] != -1).any())
            rule_mask = df["idx"].isin(skipped_idx)

        df_llm = df[~rule_mask.reindex(df.index, fill_value=False)]
        df_sample = df_llm[df_llm["amount"].abs() >= cfg.min_abs_amount_for_llm] if profile.clustering_available else df_llm
        self.report(0.24, "Выбираю представителей групп операций")
        transactions, cluster_strategy = select_for_llm(df_sample, space, cfg)
        n_rule = int(rule_mask.sum())
        if n_rule:
            notes.append(f"Операций старше {cfg.rule_skip_older_than_years} лет до заявления (вне периодов подозрительности): {n_rule} — "
                         "риск 0 по правилу.")

        outcomes: dict[str, dict] = {}
        if rt.use_llm:
            analysis = BatchAnalysis(rt, profile)
            self.report(0.26, f"Анализирую операции (GigaChat): {len(transactions)} на первом проходе")
            direct = self.run_batches(transactions, analysis, profile, "Первый проход", 0.26, 0.66)
            outcomes = {idx: _outcome(res, "llm", idx, 1.0, "high", False) for idx, res in direct.items() if _has_valid_risk(res)}
        else:
            self.report(0.30, "Оцениваю операции по правилам (GigaChat не подключён)")
            threshold = big_amount_threshold(df)
            for record in df_llm.to_dict("records"):
                outcomes[str(record["idx"])] = _outcome(rule_based_analysis(record, threshold), "rules",
                                                        None, None, None, False)
        outcomes.update({idx: _outcome(_rule_result(cfg.rule_skip_older_than_years), "rule", None, None, None, False)
                         for idx in df.loc[rule_mask, "idx"]})

        # перенос результата на похожие операции
        self.report(0.68, "Переношу выводы на похожие операции")
        pending = df[~df["idx"].isin(outcomes)]
        skipped_clusters = {cid for cid, s in cluster_strategy.items() if s["skipped"]}
        # Правило и ответы без валидного risk_level на другие операции не переносятся.
        pool = df[df["idx"].isin([idx for idx, o in outcomes.items() if o["analysis_source"] == "llm"])]
        for idx in pending.loc[pending["cluster"].isin(skipped_clusters), "idx"]:
            outcomes[idx] = _outcome(EMPTY_ANALYSIS, "none", None, None, None, True)
        pending = pending[~pending["cluster"].isin(skipped_clusters)]
        if not pending.empty and not pool.empty:
            # блоками: матрица pending × pool на десятках тысяч операций не помещается в память целиком
            blocks = [space.matrix(pending.iloc[i:i + 2000], pool) for i in range(0, len(pending), 2000)]
            best_pos = np.concatenate([b.argmax(axis=1) for b in blocks])
            best_sim = np.concatenate([b.max(axis=1) for b in blocks])
            pool_records = pool.to_dict("records")
            for record, pos, sim in zip(pending.to_dict("records"), best_pos, best_sim):
                sim = float(sim)
                if sim < PROPAGATION_CONFIDENCE_LOW:
                    outcomes[record["idx"]] = _outcome(EMPTY_ANALYSIS, "none", None, round(sim, 4), None, True)
                    continue
                representative = pool_records[int(pos)]
                analysis_row = outcomes[representative["idx"]]
                confidence = "high" if sim >= PROPAGATION_CONFIDENCE_HIGH else "medium"
                if (confidence == "high" and analysis_row["risk_level"] == 1
                        and resolve_counterparty_inn(record) != resolve_counterparty_inn(representative)):
                    confidence = "medium"
                outcomes[record["idx"]] = _outcome(analysis_row, "propagated", representative["idx"], round(sim, 4),
                                                   confidence, False)
        for idx in pending["idx"]:
            outcomes.setdefault(idx, _outcome(EMPTY_ANALYSIS, "none", None, None, None, True))

        # повторные проходы и гарантия покрытия
        if rt.use_llm:
            analysis = BatchAnalysis(rt, profile)
            for pass_num in range(0, cfg.max_guarantee_passes + 1):
                missing_idx = [idx for idx, o in outcomes.items() if o["needs_review"]]
                if not missing_idx:
                    break
                label = "Второй проход" if pass_num == 0 else f"Гарантийный проход {pass_num}"
                start = 0.70 + 0.05 * pass_num
                self.report(start, f"{label}: операций {len(missing_idx)}")
                for idx, res in self.run_batches(df[df["idx"].isin(missing_idx)], analysis, profile, label,
                                                 start, start + 0.05).items():
                    if _has_valid_risk(res):
                        outcomes[idx] = _outcome(res, "llm_second_pass", idx, 1.0, "high", False)
        for idx, outcome in outcomes.items():
            if outcome["needs_review"]:
                outcome.update(
                    risk_level=1, analysis_source="manual_review_required",
                    risk_explanation="Автоматический анализ не дал результата после повторных попыток.",
                    recommendation="Проверить операцию вручную.",
                    decision_argumentation="Автоматический анализ не дал результата: операцию нужно проверить вручную.",
                )
        n_manual = sum(o["analysis_source"] == "manual_review_required" for o in outcomes.values())
        if n_manual:
            notes.append(f"Без результата автоматического анализа: {n_manual} операций — они отмечены для ручной проверки.")

        full = df.merge(pd.DataFrame.from_dict(outcomes, orient="index").rename_axis("idx").reset_index(),
                        on="idx", how="left", validate="one_to_one")
        full = full.sort_values(["date", "idx"], kind="mergesort", na_position="last").reset_index(drop=True)

        memory_stats: dict = {}
        if rt.risk_db is not None and rt.use_llm:
            self.report(0.96, "Обновляю память риска контрагентов")
            memory_stats = update_risk_memory_from_results(rt.risk_db, full, cfg.court_filing_date)
        self.report(1.0, "Анализ завершён")
        return AnalysisResult(
            full_analysis=full, rejected=prepared.rejected, notes=notes,
            stats={"sources": full["analysis_source"].value_counts().to_dict(), "sanitized_texts": SanitizeCounter.count,
                   "llm_operations_first_pass": len(transactions)},
            cluster_strategy=cluster_strategy, tokens=rt.token_summary(), memory_update=memory_stats, profile=profile)


def run_analysis(raw: pd.DataFrame, rt: Runtime, *, spark_df: pd.DataFrame | None = None,
                 progress: ProgressFn | None = None, cancel: threading.Event | None = None,
                 disable_relations: bool = False, disable_clustering: bool = False) -> AnalysisResult:
    return AnalysisPipeline(rt, progress, cancel).run(raw, spark_df, disable_relations=disable_relations,
                                                      disable_clustering=disable_clustering)
