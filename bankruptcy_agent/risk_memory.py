from __future__ import annotations

import sqlite3
import math
from datetime import datetime, date

class RiskMemoryDB:
    def __init__(self, db_path: str = "risk_memory.db"):
        self.conn = sqlite3.connect(db_path)
        self.cursor = self.conn.cursor()
        self._create_tables()

    def _create_tables(self):
        self.cursor.execute("""
        CREATE TABLE IF NOT EXISTS risk_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            inn TEXT NOT NULL,
            risk_level INTEGER NOT NULL CHECK (risk_level IN (2, 3)),
            operation_date TEXT NOT NULL,
            court_filing_date TEXT NOT NULL,
            reason TEXT
        )
        """)

        self.cursor.execute("""
        CREATE TABLE IF NOT EXISTS counterparty_risk (
            inn TEXT NOT NULL,
            court_filing_date TEXT NOT NULL,
            risk_score REAL NOT NULL,
            risk_score_percent REAL NOT NULL,
            risk_grade TEXT NOT NULL,
            events_count INTEGER NOT NULL,
            last_operation_date TEXT,
            PRIMARY KEY (inn, court_filing_date)
        )
        """)

        self.conn.commit()

    def add_risk_event(
        self,
        inn: str,
        risk_level: int,
        operation_date: str,
        court_filing_date: str | None = None,
        reason: str | None = None
    ):
        if risk_level not in (2, 3):
            raise ValueError("risk_level должен быть 2 или 3")

        self._validate_date(operation_date)

        if court_filing_date is None:
            court_filing_date = date.today().isoformat()
        else:
            self._validate_date(court_filing_date)

        self.cursor.execute("""
        INSERT INTO risk_events (
            inn,
            risk_level,
            operation_date,
            court_filing_date,
            reason
        )
        VALUES (?, ?, ?, ?, ?)
        """, (
            inn,
            risk_level,
            operation_date,
            court_filing_date,
            reason
        ))

        self._recalculate_counterparty_score(
            inn=inn,
            court_filing_date=court_filing_date
        )

        self.conn.commit()

    def _recalculate_counterparty_score(
        self,
        inn: str,
        court_filing_date: str
    ):
        self.cursor.execute("""
        SELECT
            risk_level,
            operation_date
        FROM risk_events
        WHERE inn = ?
          AND court_filing_date = ?
        """, (
            inn,
            court_filing_date
        ))

        events = self.cursor.fetchall()

        if not events:
            return

        court_dt = datetime.fromisoformat(court_filing_date).date()

        lambda_decay = -math.log(0.4) / 365

        weighted_sum = 0.0
        max_weighted_sum = 0.0
        valid_operation_dates = []

        for risk_level, operation_date_str in events:
            operation_dt = datetime.fromisoformat(operation_date_str).date()

            days_before_filing = (court_dt - operation_dt).days

            if days_before_filing < 0:
                continue

            time_weight = math.exp(-lambda_decay * days_before_filing)

            weighted_sum += risk_level * time_weight
            max_weighted_sum += 3 * time_weight
            valid_operation_dates.append(operation_date_str)

        if max_weighted_sum == 0:
            risk_score = 0.0
            risk_score_percent = 0.0
            risk_grade = "NO_VALID_EVENTS"
            events_count = 0
            last_operation_date = None
        else:
            risk_score = weighted_sum / max_weighted_sum
            risk_score_percent = risk_score * 100
            risk_grade = self._get_risk_grade(risk_score)
            events_count = len(valid_operation_dates)
            last_operation_date = max(valid_operation_dates)

        self.cursor.execute("""
        INSERT INTO counterparty_risk (
            inn,
            court_filing_date,
            risk_score,
            risk_score_percent,
            risk_grade,
            events_count,
            last_operation_date
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(inn, court_filing_date) DO UPDATE SET
            risk_score = excluded.risk_score,
            risk_score_percent = excluded.risk_score_percent,
            risk_grade = excluded.risk_grade,
            events_count = excluded.events_count,
            last_operation_date = excluded.last_operation_date
        """, (
            inn,
            court_filing_date,
            risk_score,
            risk_score_percent,
            risk_grade,
            events_count,
            last_operation_date
        ))

    def _get_risk_grade(self, risk_score: float) -> str:
        if risk_score == 0:
            return "NO_VALID_EVENTS"
        elif risk_score < 0.70:
            return "LOW"
        elif risk_score < 0.85:
            return "MEDIUM"
        elif risk_score < 0.95:
            return "HIGH"
        else:
            return "CRITICAL"

    def get_conterparty_risk(
        self,
        inn: str,
        court_filing_date: str | None = None
    ):
        if court_filing_date is None:
            court_filing_date = date.today().isoformat()
        else:
            self._validate_date(court_filing_date)

        self.cursor.execute("""
        SELECT
            inn,
            court_filing_date,
            risk_score,
            risk_score_percent,
            risk_grade,
            events_count,
            last_operation_date
        FROM counterparty_risk
        WHERE inn = ?
          AND court_filing_date = ?
        """, (
            inn,
            court_filing_date
        ))

        return self.cursor.fetchone()

    def get_risk_events(
        self,
        inn: str,
        court_filing_date: str | None = None,
        limit: int = 5
    ):
        if court_filing_date is None:
            court_filing_date = date.today().isoformat()
        else:
            self._validate_date(court_filing_date)

        self.cursor.execute(f"""
        SELECT
            id,
            inn,
            risk_level,
            operation_date,
            court_filing_date,
            reason
        FROM risk_events
        WHERE inn = ?
          AND court_filing_date = ?
        ORDER BY operation_date DESC
        LIMIT {limit}
        """, (
            inn,
            court_filing_date
        ))

        return self.cursor.fetchall()

    def _validate_date(self, value: str):
        try:
            datetime.fromisoformat(value).date()
        except ValueError:
            raise ValueError("Дата должна быть строкой в формате YYYY-MM-DD")

    def close(self):
        self.conn.close()

