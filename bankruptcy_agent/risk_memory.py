from __future__ import annotations

import logging
import math
import sqlite3
import threading
from datetime import date, datetime
from typing import Any

import pandas as pd

logger = logging.getLogger("bankruptcy_agent.risk_memory")


class RiskMemoryDB:
    """Динамическая историческая память риска контрагентов.

    Портировано из notebooks/agent_v3_adaptive_clusters_1.ipynb (ячейка 17).
    Источник истины — журнал событий `risk_events`; агрегированный риск —
    функция от событий, вычисляемая на момент чтения (`as_of`), а не статичное
    накопленное число:

        s_i   = severity(risk_level) * proximity(операция<->банкротство) * recency(давность подтверждения)
        score = 1 - Π(1 - s_i)          # noisy-OR по событиям

    - severity: risk 3 качественно сильнее risk 2 (1.0 против 0.55);
    - proximity: экспоненциальное затухание по |дней между операцией и датой
      заявления о банкротстве|;
    - recency: экспоненциальное затухание по давности последнего подтверждения
      события (полураспад RECENCY_HALF_LIFE_DAYS);
    - noisy-OR: каждое новое независимое событие увеличивает риск, при этом
      множество слабых старых событий не «размывает» сильное свежее.
    """

    SEVERITY = {2: 0.55, 3: 1.0}
    PROXIMITY_WEIGHT_AT_ONE_YEAR = 0.4
    RECENCY_HALF_LIFE_DAYS = 180

    def __init__(self, db_path: str = "risk_memory.db"):
        # check_same_thread=False: Gradio обрабатывает callback-и в worker-потоках,
        # отличных от потока, в котором risk_db создается при импорте models.py.
        # _lock сериализует доступ к общему cursor/conn между запросами.
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.cursor = self.conn.cursor()
        self._lock = threading.RLock()
        self._create_tables()

    def _create_tables(self) -> None:
        self.cursor.execute("""
        CREATE TABLE IF NOT EXISTS risk_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            inn TEXT NOT NULL,
            operation_idx TEXT NOT NULL DEFAULT '',
            risk_level INTEGER NOT NULL CHECK (risk_level IN (2, 3)),
            operation_date TEXT NOT NULL,
            court_filing_date TEXT NOT NULL,
            reason TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE (inn, court_filing_date, operation_idx)
        )
        """)
        self.cursor.execute("""
        CREATE TABLE IF NOT EXISTS counterparty_risk_snapshot (
            inn TEXT PRIMARY KEY,
            as_of TEXT NOT NULL,
            risk_score REAL NOT NULL,
            risk_grade TEXT NOT NULL,
            events_count INTEGER NOT NULL,
            last_event_date TEXT,
            reasoning TEXT,
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """)
        self.conn.commit()

    def add_risk_event(
        self,
        inn: str,
        risk_level: int,
        operation_date: str,
        court_filing_date: str | None = None,
        reason: str | None = None,
        operation_idx: str = "",
    ) -> None:
        """Записывает (или обновляет при повторном анализе) риск-событие.

        Ключ (inn, court_filing_date, operation_idx): повторный запуск агента
        по тем же данным обновляет risk_level/reason/updated_at существующей
        записи, а не создает дубль.
        """
        if risk_level not in (2, 3):
            raise ValueError("risk_level должен быть 2 или 3")
        inn = str(inn).strip()
        if not inn:
            raise ValueError("inn не должен быть пустым")

        self._validate_date(operation_date)
        if court_filing_date is None:
            court_filing_date = date.today().isoformat()
        else:
            self._validate_date(court_filing_date)

        with self._lock:
            if not operation_idx:
                logger.warning("add_risk_event без operation_idx (inn=%s): дедупликация невозможна.", inn)
                self.cursor.execute(
                    """INSERT INTO risk_events
                       (inn, operation_idx, risk_level, operation_date, court_filing_date, reason)
                       VALUES (?, '', ?, ?, ?, ?)""",
                    (inn, risk_level, operation_date, court_filing_date, reason),
                )
            else:
                self.cursor.execute(
                    """INSERT INTO risk_events
                       (inn, operation_idx, risk_level, operation_date, court_filing_date, reason)
                       VALUES (?, ?, ?, ?, ?, ?)
                       ON CONFLICT (inn, court_filing_date, operation_idx) DO UPDATE SET
                           risk_level = excluded.risk_level,
                           operation_date = excluded.operation_date,
                           reason = excluded.reason,
                           updated_at = datetime('now')""",
                    (inn, str(operation_idx), risk_level, operation_date, court_filing_date, reason),
                )

            self._refresh_snapshot(inn)
            self.conn.commit()

    def compute_current_risk(self, inn: str, as_of: str | None = None) -> dict[str, Any]:
        """Считает текущий агрегированный риск контрагента на дату as_of
        (по умолчанию — сегодня) по всем делам, в которых он встречался.
        """
        inn = str(inn).strip()
        as_of_date = date.today() if as_of is None else datetime.fromisoformat(as_of).date()

        with self._lock:
            rows = self.cursor.execute(
                """SELECT risk_level, operation_date, court_filing_date, reason, updated_at
                   FROM risk_events WHERE inn = ?""",
                (inn,),
            ).fetchall()

        if not rows:
            return {
                "inn": inn,
                "as_of": as_of_date.isoformat(),
                "risk_score": 0.0,
                "risk_grade": "NO_HISTORY",
                "events_count": 0,
                "explanation": "История риск-событий по контрагенту отсутствует.",
            }

        lambda_prox = -math.log(self.PROXIMITY_WEIGHT_AT_ONE_YEAR) / 365
        lambda_rec = math.log(2) / self.RECENCY_HALF_LIFE_DAYS

        contributions = []
        recent_180d = 0
        last_event_date = None

        for row in rows:
            op_dt = datetime.fromisoformat(row["operation_date"]).date()
            case_dt = datetime.fromisoformat(row["court_filing_date"]).date()
            confirmed_dt = datetime.fromisoformat(row["updated_at"]).date()

            severity = self.SEVERITY[row["risk_level"]]
            proximity = math.exp(-lambda_prox * abs((case_dt - op_dt).days))
            days_since_confirmed = max((as_of_date - confirmed_dt).days, 0)
            recency = math.exp(-lambda_rec * days_since_confirmed)

            s_i = min(max(severity * proximity * recency, 0.0), 0.99)
            contributions.append({
                "s_i": round(s_i, 4),
                "risk_level": row["risk_level"],
                "operation_date": row["operation_date"],
                "court_filing_date": row["court_filing_date"],
                "reason": row["reason"],
                "proximity": round(proximity, 3),
                "recency": round(recency, 3),
            })

            if days_since_confirmed <= 180:
                recent_180d += 1
            if last_event_date is None or row["operation_date"] > last_event_date:
                last_event_date = row["operation_date"]

        score = 1.0
        for c in contributions:
            score *= (1.0 - c["s_i"])
        score = 1.0 - score

        top = sorted(contributions, key=lambda c: c["s_i"], reverse=True)[:3]
        days_since_last = (as_of_date - datetime.fromisoformat(last_event_date).date()).days

        explanation = (
            f"Событий в истории: {len(contributions)} (за последние 180 дней подтверждено: {recent_180d}). "
            f"Последняя рискованная операция: {last_event_date} ({days_since_last} дн. назад). "
            "Сильнейшие вклады: "
            + "; ".join(
                f"[{c['operation_date']}, risk {c['risk_level']}, вклад {c['s_i']:.2f}] {c['reason'] or 'без обоснования'}"
                for c in top
            )
        )

        return {
            "inn": inn,
            "as_of": as_of_date.isoformat(),
            "risk_score": round(score, 4),
            "risk_grade": self._get_risk_grade(score),
            "events_count": len(contributions),
            "recent_events_180d": recent_180d,
            "last_event_date": last_event_date,
            "days_since_last_event": days_since_last,
            "top_contributions": top,
            "explanation": explanation,
        }

    @staticmethod
    def _get_risk_grade(risk_score: float) -> str:
        if risk_score <= 0:
            return "NO_HISTORY"
        if risk_score < 0.35:
            return "LOW"
        if risk_score < 0.65:
            return "MEDIUM"
        if risk_score < 0.95:
            return "HIGH"
        return "CRITICAL"

    def _refresh_snapshot(self, inn: str) -> None:
        current = self.compute_current_risk(inn)
        self.cursor.execute(
            """INSERT INTO counterparty_risk_snapshot
               (inn, as_of, risk_score, risk_grade, events_count, last_event_date, reasoning, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
               ON CONFLICT (inn) DO UPDATE SET
                   as_of = excluded.as_of,
                   risk_score = excluded.risk_score,
                   risk_grade = excluded.risk_grade,
                   events_count = excluded.events_count,
                   last_event_date = excluded.last_event_date,
                   reasoning = excluded.reasoning,
                   updated_at = datetime('now')""",
            (
                current["inn"], current["as_of"], current["risk_score"], current["risk_grade"],
                current["events_count"], current.get("last_event_date"), current.get("explanation"),
            ),
        )

    def get_risk_events(self, inn: str, court_filing_date: str | None = None, limit: int = 5) -> list[dict[str, Any]]:
        """Последние события по контрагенту (с обоснованиями)."""
        inn = str(inn).strip()
        with self._lock:
            if court_filing_date is not None:
                self._validate_date(court_filing_date)
                rows = self.cursor.execute(
                    """SELECT operation_idx, risk_level, operation_date, court_filing_date, reason
                       FROM risk_events WHERE inn = ? AND court_filing_date = ?
                       ORDER BY operation_date DESC LIMIT ?""",
                    (inn, court_filing_date, int(limit)),
                ).fetchall()
            else:
                rows = self.cursor.execute(
                    """SELECT operation_idx, risk_level, operation_date, court_filing_date, reason
                       FROM risk_events WHERE inn = ?
                       ORDER BY operation_date DESC LIMIT ?""",
                    (inn, int(limit)),
                ).fetchall()
        return [dict(row) for row in rows]

    def list_known_inns(self) -> list[str]:
        """Все ИНН, по которым есть хотя бы один снимок риска (для UI)."""
        with self._lock:
            rows = self.cursor.execute("SELECT inn FROM counterparty_risk_snapshot").fetchall()
        return [row["inn"] for row in rows]

    @staticmethod
    def _validate_date(value: str) -> None:
        try:
            datetime.fromisoformat(value).date()
        except (ValueError, TypeError):
            raise ValueError("Дата должна быть строкой в формате YYYY-MM-DD")

    def close(self) -> None:
        self.conn.close()


def update_counterparty_risk_memory(
    risk_db: RiskMemoryDB,
    *,
    inn: str,
    risk_level: int,
    operation_date: str,
    reason: str,
    court_filing_date: str | None = None,
    operation_idx: str = "",
) -> dict[str, Any]:
    """Записать подтвержденное риск-событие в долгосрочную память контрагентов
    (ячейка 18 тетрадки: тонкая детерминированная обертка над `add_risk_event`).

    Вызывается пайплайном ПОСЛЕ финального анализа, только для итогового
    risk_level 2 или 3. Намеренно не оформлена как LLM-tool: запись в память —
    детерминированный шаг пайплайна, LLM не должна решать, что попадает в историю.
    """
    if risk_level not in (2, 3):
        raise ValueError("В память можно записывать только risk_level 2 или 3")
    risk_db.add_risk_event(
        inn=inn,
        risk_level=risk_level,
        operation_date=operation_date,
        court_filing_date=court_filing_date,
        reason=reason,
        operation_idx=operation_idx,
    )
    return {"status": "Память обновлена", "inn": inn, "operation_idx": operation_idx}


def update_risk_memory_from_results(
    risk_db: RiskMemoryDB | None,
    results_df: pd.DataFrame,
    source_df: pd.DataFrame,
    court_filing_date: str,
) -> dict[str, int]:
    """Обновляет историческую память риска по итогам завершенного анализа (ячейка 43).

    Правила:
    - записываются только операции с итоговым risk_level 2 или 3;
    - записываются только операции, проанализированные LLM напрямую
      (analysis_source in {"llm", "llm_second_pass"}): распространенные
      результаты — производные, их запись задваивала бы одно и то же
      LLM-решение по числу похожих операций и искусственно раздувала
      исторический риск (для noisy-OR это критично);
    - обоснование (reason) берется из risk_explanation (fallback — legal_qualification);
    - дедупликация и обновление при повторном анализе обеспечиваются через
      RiskMemoryDB по ключу (inn, court_filing_date, operation_idx).
    """
    stats = {"written": 0, "skipped_no_inn": 0, "skipped_low_risk": 0, "skipped_not_llm": 0, "errors": 0}
    if risk_db is None or results_df is None or results_df.empty:
        return stats

    from .similarity import resolve_counterparty_inn

    date_by_idx = (
        source_df.set_index(source_df["idx"].astype(str))["date"]
        if "idx" in source_df.columns and "date" in source_df.columns
        else pd.Series(dtype=object)
    )

    for _, row in results_df.iterrows():
        if row.get("analysis_source", "llm") not in ("llm", "llm_second_pass"):
            stats["skipped_not_llm"] += 1
            continue

        try:
            risk_level = int(float(row.get("risk_level")))
        except (TypeError, ValueError):
            stats["skipped_low_risk"] += 1
            continue
        if risk_level not in (2, 3):
            stats["skipped_low_risk"] += 1
            continue

        inn = resolve_counterparty_inn(row)
        if inn is None:
            stats["skipped_no_inn"] += 1
            continue

        op_idx = str(row.get("idx") or "")
        op_date = row.get("date")
        if (op_date is None or pd.isna(op_date)) and op_idx in date_by_idx.index:
            op_date = date_by_idx.loc[op_idx]
        if op_date is None or (isinstance(op_date, float) and pd.isna(op_date)):
            op_date = court_filing_date
        try:
            operation_date = pd.to_datetime(op_date).date().isoformat()
        except (ValueError, TypeError):
            operation_date = court_filing_date

        reason = str(row.get("risk_explanation") or row.get("legal_qualification") or "")[:500]

        try:
            update_counterparty_risk_memory(
                risk_db,
                inn=inn,
                risk_level=risk_level,
                operation_date=operation_date,
                reason=reason,
                court_filing_date=court_filing_date,
                operation_idx=op_idx,
            )
            stats["written"] += 1
        except Exception:
            logger.exception("Не удалось записать риск-событие: inn=%s, idx=%s", inn, op_idx)
            stats["errors"] += 1

    logger.info("Обновление памяти риска завершено: %s", stats)
    return stats
