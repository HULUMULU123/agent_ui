"""Сервис парсера выписок: файл любого формата -> таблица операций для агента + отчёт о качестве распознавания.

Парсер хранит состояние в глобальных переменных модуля, поэтому запуски сериализуются блокировкой.
Если загружена уже нормализованная таблица (колонки date, amount, purpose и ИНН/наименования сторон), парсер
не запускается — таблица идёт в анализ как есть.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import pandas as pd

logger = logging.getLogger("finapp.parser")

_PARSE_LOCK = threading.Lock()
SUPPORTED_SUFFIXES = (".xlsx", ".xlsm", ".xls", ".csv", ".txt", ".html", ".htm", ".docx", ".pdf")
READY_REQUIRED = {"date", "amount", "purpose"}
READY_PARTY = {"debit_inn", "credit_inn", "debit_name", "credit_name"}

STATUS_LABELS = {
    "ok": "Распознано, итоги выписки сошлись",
    "ok_unverified": "Распознано, итоги выписки не удалось сверить",
    "needs_review": "Распознано с замечаниями — проверьте результат",
    "ready_table": "Загружена готовая таблица операций",
}


@dataclass
class ParseOutcome:
    table: pd.DataFrame
    status: str = "ok"
    owner_inn: str = ""
    owner_name: str = ""
    mode: str = ""
    errors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    checks: list[dict] = field(default_factory=list)
    excluded_rows: int = 0
    used_llm: bool = False
    log: list[str] = field(default_factory=list)

    @property
    def status_label(self) -> str:
        return STATUS_LABELS.get(self.status, self.status)

    def summary(self) -> dict[str, Any]:
        return {"status": self.status, "status_label": self.status_label, "owner_inn": self.owner_inn,
                "owner_name": self.owner_name, "mode": self.mode, "errors": self.errors, "notes": self.notes,
                "checks": self.checks, "excluded_rows": self.excluded_rows, "used_llm": self.used_llm,
                "operations": int(len(self.table))}


def _read_ready_table(path: Path) -> pd.DataFrame | None:
    """Таблица в формате агента (не выписка банка), если файл именно такой."""
    suffix = path.suffix.lower()
    try:
        if suffix in (".xlsx", ".xlsm", ".xls"):
            frame = pd.read_excel(path)
        elif suffix == ".csv":
            frame = pd.read_csv(path, sep=None, engine="python", encoding_errors="replace")
        else:
            return None
    except Exception:  # noqa: BLE001
        return None
    columns = {str(c).strip().lower() for c in frame.columns}
    has_amount = "amount" in columns or {"debit_amount", "credit_amount"} & columns
    if READY_REQUIRED - {"amount"} <= columns and has_amount and READY_PARTY & columns:
        frame.columns = [str(c).strip().lower() for c in frame.columns]
        return frame
    return None


def guess_owner(table: pd.DataFrame) -> tuple[str, str]:
    """Владелец счёта в готовой таблице — сторона, которая есть почти в каждой операции: самый частый ИНН (или наименование)."""
    from collections import Counter

    for inn_col, name_col in (("debit_inn", "debit_name"), ("credit_inn", "credit_name")):
        if inn_col not in table.columns:
            table[inn_col] = ""
        if name_col not in table.columns:
            table[name_col] = ""
    counter: Counter[str] = Counter()
    names: dict[str, str] = {}
    for inn_col, name_col in (("debit_inn", "debit_name"), ("credit_inn", "credit_name")):
        for inn, name in zip(table[inn_col].astype(str), table[name_col].astype(str)):
            inn = inn.strip().removesuffix(".0")
            if inn and inn.lower() not in ("nan", "none", "<na>"):
                counter[inn] += 1
                if name.strip() and name.lower() not in ("nan", "none", "<na>"):
                    names.setdefault(inn, name.strip())
    if not counter:
        return "", ""
    inn, hits = counter.most_common(1)[0]
    if hits < max(2, int(0.6 * len(table))):
        return "", ""  # единого владельца не видно: лучше не угадывать
    return inn, names.get(inn, "")


def parse_statement_file(path: str | Path, llm: Any | None = None, log: Callable[[str], None] | None = None) -> ParseOutcome:
    """Разбирает выписку. llm — клиент GigaChat-3-Ultra (None — только алгоритм). Исключение — если файл не прочитан."""
    path = Path(path)
    if path.suffix.lower() not in SUPPORTED_SUFFIXES:
        raise ValueError(f"Формат {path.suffix or '(нет расширения)'} не поддерживается. Поддерживаются: "
                         + ", ".join(SUPPORTED_SUFFIXES))
    ready = _read_ready_table(path)
    if ready is not None:
        owner_inn, owner_name = guess_owner(ready)
        return ParseOutcome(table=ready, status="ready_table", mode="ready_table", owner_inn=owner_inn, owner_name=owner_name,
                            notes=["Файл уже содержит таблицу операций в нужном формате — распознавание выписки не потребовалось."])

    from . import bank_parser as bp

    collected: list[str] = []

    def hook(message: str) -> None:
        collected.append(message)
        if log is not None:
            log(message)

    with _PARSE_LOCK:
        bp.VERBOSE = False
        bp.LOG_HOOK = hook
        try:
            table, report = bp.parse_statement_auto(path, llm=llm, use_llm=llm is not None)
        finally:
            bp.LOG_HOOK = None
    owner = report.get("owner") or {}
    outcome = ParseOutcome(
        table=table, status=str(report.get("status") or "ok"), owner_inn=str(owner.get("inn") or ""),
        owner_name=str(owner.get("name") or ""), mode=str(report.get("mode") or ""),
        errors=[str(e) for e in report.get("errors") or []], notes=[str(n) for n in report.get("notes") or []],
        checks=[{k: c.get(k) for k in ("check", "status", "level", "detail")} for c in report.get("checks") or []],
        excluded_rows=len(report.get("excluded_rows") or []), used_llm=llm is not None, log=collected)
    if table.empty:
        raise ValueError("В файле не найдено ни одной операции. " + (" ".join(outcome.errors) or "Проверьте, что это банковская выписка."))
    return outcome
