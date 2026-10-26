"""Память риска контрагентов (SQLite): журнал событий risk_events и текущий риск noisy-OR.

Ключ события (inn, court_filing_date, operation_idx): повторный анализ той же операции обновляет запись.
s = proximity × recency; proximity — затухание по |дням между операцией и заявлением| (вес 0.4 на горизонте года),
recency — полураспад 180 дней от последнего подтверждения; итог — noisy-OR 1 − Π(1 − sᵢ).
Пишутся только операции с итоговым risk_level = 1, проанализированные LLM напрямую.
"""
from __future__ import annotations

import logging
import math
import re
import shutil
import sqlite3
import threading
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from .common import is_missing, normalize_inn, parse_dates, resolve_counterparty_inn, to_risk_level

logger = logging.getLogger("finapp.agent.risk_memory")

RISK_GRADE_LABELS = {"NO_HISTORY": "нет истории", "LOW": "низкий", "MEDIUM": "средний",
                     "HIGH": "высокий", "CRITICAL": "очень высокий"}


class RiskMemoryDB:
    PROXIMITY_WEIGHT_AT_ONE_YEAR = 0.4
    RECENCY_HALF_LIFE_DAYS = 180

    def __init__(self, db_path: str | Path = "risk_memory.db"):
        # Инструменты вызываются из рабочих потоков: check_same_thread=False + RLock.
        self._lock = threading.RLock()
        self.db_path = Path(db_path)
        self._migrate_legacy_schema()
        self.conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._create_tables()

    def _migrate_legacy_schema(self) -> None:
        """Старые базы создавались с CHECK (risk_level IN (2, 3)): текущий код пишет только risk_level = 1, а UPDATE
        старых строк упирается в это ограничение. Пересоздаём таблицу (2 и 3 -> 1), перед этим кладём копию файла рядом."""
        if not self.db_path.exists():
            return
        probe = sqlite3.connect(str(self.db_path))
        try:
            row = probe.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'risk_events'").fetchone()
            if not row or not re.search(r"risk_level\s+IN\s*\(", row[0] or "", re.IGNORECASE):
                return
            backup = self.db_path.with_name(f"{self.db_path.name}.bak-{datetime.now():%Y%m%d-%H%M%S}")
            shutil.copyfile(self.db_path, backup)
            logger.warning("risk_memory.db: старая схема, выполняю миграцию (копия: %s)", backup.name)
            probe.executescript("""
                BEGIN;
                ALTER TABLE risk_events RENAME TO risk_events_legacy;
                CREATE TABLE risk_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    inn TEXT NOT NULL,
                    operation_idx TEXT NOT NULL DEFAULT '',
                    risk_level INTEGER NOT NULL CHECK (risk_level = 1),
                    operation_date TEXT NOT NULL,
                    court_filing_date TEXT NOT NULL,
                    reason TEXT,
                    created_at TEXT NOT NULL DEFAULT (datetime('now')),
                    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                    UNIQUE (inn, court_filing_date, operation_idx)
                );
                INSERT OR IGNORE INTO risk_events (inn, operation_idx, risk_level, operation_date, court_filing_date, reason,
                                                   created_at, updated_at)
                    SELECT inn, operation_idx, 1, operation_date, court_filing_date, reason, created_at, updated_at
                    FROM risk_events_legacy WHERE risk_level >= 1;
                DROP TABLE risk_events_legacy;
                COMMIT;
            """)
        finally:
            probe.close()

    def _create_tables(self) -> None:
        with self._lock:
            self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS risk_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                inn TEXT NOT NULL,
                operation_idx TEXT NOT NULL DEFAULT '',
                risk_level INTEGER NOT NULL CHECK (risk_level = 1),
                operation_date TEXT NOT NULL,
                court_filing_date TEXT NOT NULL,
                reason TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                UNIQUE (inn, court_filing_date, operation_idx)
            );
            CREATE TABLE IF NOT EXISTS counterparty_risk_snapshot (
                inn TEXT PRIMARY KEY,
                as_of TEXT NOT NULL,
                risk_score REAL NOT NULL,
                risk_grade TEXT NOT NULL,
                events_count INTEGER NOT NULL,
                last_event_date TEXT,
                reasoning TEXT,
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            """)
            cols = {row["name"] for row in self.conn.execute("PRAGMA table_info(risk_events)")}
            if not {"operation_idx", "updated_at"} <= cols:
                raise RuntimeError("risk_memory.db создан старой версией схемы: удалите файл и перезапустите приложение.")
            self.conn.execute("UPDATE risk_events SET risk_level = 1 WHERE risk_level > 1")
            self.conn.execute("DELETE FROM risk_events WHERE risk_level < 1")
            self.conn.commit()

    @staticmethod
    def _iso_date(value: Any) -> str:
        """date/Timestamp/'ГГГГ-ММ-ДД'/'ДД.ММ.ГГГГ' -> 'ГГГГ-ММ-ДД'."""
        if isinstance(value, (datetime, pd.Timestamp)):
            return value.date().isoformat()
        if isinstance(value, date):
            return value.isoformat()
        parsed = parse_dates([value]).iloc[0]
        if pd.isna(parsed):
            raise ValueError(f"Нераспознанная дата: {value!r}")
        return parsed.date().isoformat()

    def add_risk_event(self, inn: str, operation_date: Any, court_filing_date: Any,
                       reason: str | None = None, operation_idx: str = "") -> None:
        inn = normalize_inn(inn)
        if not inn:
            raise ValueError("inn не должен быть пустым")
        params = (inn, str(operation_idx), self._iso_date(operation_date), self._iso_date(court_filing_date), reason)
        with self._lock:
            self.conn.execute(
                """INSERT INTO risk_events (inn, operation_idx, risk_level, operation_date, court_filing_date, reason)
                   VALUES (?, ?, 1, ?, ?, ?)
                   ON CONFLICT (inn, court_filing_date, operation_idx) DO UPDATE SET
                       operation_date = excluded.operation_date,
                       reason = excluded.reason,
                       updated_at = datetime('now')""",
                params,
            )
            self._refresh_snapshot(inn)
            self.conn.commit()

    def compute_current_risk(self, inn: str, as_of: str | None = None) -> dict:
        inn = normalize_inn(inn)
        as_of_date = date.today() if as_of is None else datetime.fromisoformat(as_of).date()
        with self._lock:
            rows = self.conn.execute(
                "SELECT operation_date, court_filing_date, reason, updated_at FROM risk_events WHERE inn = ?", (inn,),
            ).fetchall()
        if not rows:
            return {"inn": inn, "as_of": as_of_date.isoformat(), "risk_score": 0.0,
                    "risk_grade": "NO_HISTORY", "events_count": 0, "top_contributions": []}

        lambda_prox = -math.log(self.PROXIMITY_WEIGHT_AT_ONE_YEAR) / 365
        lambda_rec = math.log(2) / self.RECENCY_HALF_LIFE_DAYS
        contributions, recent_180d = [], 0
        for row in rows:
            op_dt = date.fromisoformat(row["operation_date"])
            case_dt = date.fromisoformat(row["court_filing_date"])
            days_since_confirmed = max((as_of_date - datetime.fromisoformat(row["updated_at"]).date()).days, 0)
            s_i = math.exp(-lambda_prox * abs((case_dt - op_dt).days)) * math.exp(-lambda_rec * days_since_confirmed)
            contributions.append({"s_i": min(max(s_i, 0.0), 0.99), "operation_date": row["operation_date"],
                                  "reason": row["reason"]})
            recent_180d += days_since_confirmed <= 180

        score = 1.0 - math.prod(1.0 - c["s_i"] for c in contributions)
        return {
            "inn": inn,
            "as_of": as_of_date.isoformat(),
            "risk_score": round(score, 4),
            "risk_grade": self._get_risk_grade(score),
            "events_count": len(contributions),
            "recent_events_180d": recent_180d,
            "last_event_date": max(c["operation_date"] for c in contributions),
            "top_contributions": sorted(contributions, key=lambda c: c["s_i"], reverse=True)[:3],
        }

    @staticmethod
    def _get_risk_grade(risk_score: float) -> str:
        # Одно свежее событие рядом с датой заявления даёт ~0.95; CRITICAL — только при повторяемости.
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
        reasoning = "; ".join(str(c["reason"] or "") for c in current["top_contributions"])[:1000]
        self.conn.execute(
            """INSERT INTO counterparty_risk_snapshot
               (inn, as_of, risk_score, risk_grade, events_count, last_event_date, reasoning, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
               ON CONFLICT (inn) DO UPDATE SET
                   as_of = excluded.as_of, risk_score = excluded.risk_score, risk_grade = excluded.risk_grade,
                   events_count = excluded.events_count, last_event_date = excluded.last_event_date,
                   reasoning = excluded.reasoning, updated_at = datetime('now')""",
            (current["inn"], current["as_of"], current["risk_score"], current["risk_grade"],
             current["events_count"], current.get("last_event_date"), reasoning),
        )

    def close(self) -> None:
        self.conn.close()


def update_risk_memory_from_results(risk_db: RiskMemoryDB, results: pd.DataFrame, filing_date: date) -> dict:
    """В память пишутся только операции с risk_level = 1, проанализированные LLM напрямую:
    перенесённые копии одного решения умножали бы исторический риск через noisy-OR."""
    stats = {"written": 0, "skipped_not_llm": 0, "skipped_low_risk": 0, "skipped_no_inn": 0, "errors": 0}
    for record in results.to_dict("records"):
        if record.get("analysis_source") not in ("llm", "llm_second_pass"):
            stats["skipped_not_llm"] += 1
            continue
        if to_risk_level(record.get("risk_level")) != 1:
            stats["skipped_low_risk"] += 1
            continue
        inn = resolve_counterparty_inn(record)
        if inn is None:
            stats["skipped_no_inn"] += 1
            continue
        operation_date = record["date"] if not is_missing(record.get("date")) else filing_date
        try:
            risk_db.add_risk_event(
                inn=inn, operation_date=operation_date, court_filing_date=filing_date.isoformat(),
                reason=str(record.get("risk_explanation") or record.get("legal_qualification") or "")[:500],
                operation_idx=str(record["idx"]),
            )
            stats["written"] += 1
        except Exception:  # noqa: BLE001
            logger.exception("Не удалось записать событие риска для %s", inn)
            stats["errors"] += 1
    return stats
