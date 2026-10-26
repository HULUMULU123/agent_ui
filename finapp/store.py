"""Хранилище анализов: data/analyses/<id>/{meta.json, source<ext>, analysis.pkl, reviews.json}."""
from __future__ import annotations

import json
import os
import pickle
import shutil
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from .config import DATA_DIR

ANALYSES_DIR = DATA_DIR / "analyses"

# статусы задачи
QUEUED, PARSING, ANALYZING, DONE, ERROR, CANCELLED = "queued", "parsing", "analyzing", "done", "error", "cancelled"
ACTIVE_STATUSES = {QUEUED, PARSING, ANALYZING}

REVIEW_DECISIONS = {
    "": "Не проверено",
    "confirmed": "Риск подтверждён",
    "dismissed": "Риск снят",
    "documents": "Запрошены документы",
}


@dataclass
class AnalysisBundle:
    meta: dict[str, Any]
    df: pd.DataFrame
    extras: dict[str, Any] = field(default_factory=dict)  # rejected, notes, cluster_strategy, tokens, parse_summary, stats


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _atomic_write(path: Path, data: bytes) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


class AnalysisStore:
    def __init__(self, root: Path = ANALYSES_DIR):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._bundles: dict[str, tuple[float, AnalysisBundle]] = {}

    # ── метаданные ──

    def dir(self, analysis_id: str) -> Path:
        if not analysis_id or any(ch in analysis_id for ch in "/\\.") :
            raise ValueError("Некорректный идентификатор анализа")
        return self.root / analysis_id

    def create(self, filename: str, filing_date: str, size: int = 0, **extra: Any) -> dict[str, Any]:
        analysis_id = datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
        meta = {"id": analysis_id, "filename": filename, "filing_date": filing_date, "status": QUEUED, "progress": 0.0,
                "message": "В очереди", "created_at": _now(), "finished_at": None, "size": size, "org_name": "",
                "org_inn": "", "n_ops": 0, "n_risky": 0, "n_attention": 0, "n_unclassified": 0, "period_start": "",
                "period_end": "", "mode": "", "error": "", "log": [], **extra}
        with self._lock:
            self.dir(analysis_id).mkdir(parents=True, exist_ok=True)
            self._write_meta(meta)
        return meta

    def _write_meta(self, meta: dict[str, Any]) -> None:
        _atomic_write(self.dir(meta["id"]) / "meta.json", json.dumps(meta, ensure_ascii=False, indent=1).encode("utf-8"))

    def get_meta(self, analysis_id: str) -> dict[str, Any] | None:
        try:
            return json.loads((self.dir(analysis_id) / "meta.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def update_meta(self, analysis_id: str, **values: Any) -> dict[str, Any] | None:
        with self._lock:
            meta = self.get_meta(analysis_id)
            if meta is None:
                return None
            meta.update(values)
            self._write_meta(meta)
            return meta

    def append_log(self, analysis_id: str, message: str, limit: int = 200) -> None:
        with self._lock:
            meta = self.get_meta(analysis_id)
            if meta is None:
                return
            meta["log"] = (meta.get("log") or [])[-(limit - 1):] + [f"{datetime.now():%H:%M:%S} {message}"]
            self._write_meta(meta)

    def list(self) -> list[dict[str, Any]]:
        """Все анализы, новые сверху."""
        metas = []
        for child in self.root.iterdir() if self.root.exists() else []:
            if child.is_dir():
                meta = self.get_meta(child.name)
                if meta:
                    metas.append(meta)
        return sorted(metas, key=lambda m: m.get("created_at") or "", reverse=True)

    def mark_interrupted(self) -> int:
        """После перезапуска приложения незавершённые задачи помечаются прерванными."""
        count = 0
        for meta in self.list():
            if meta["status"] in ACTIVE_STATUSES:
                self.update_meta(meta["id"], status=ERROR, error="Анализ был прерван перезапуском приложения. Загрузите файл заново.",
                                 message="Прервано")
                count += 1
        return count

    # ── результаты ──

    def source_path(self, analysis_id: str, suffix: str = "") -> Path:
        return self.dir(analysis_id) / f"source{suffix}"

    def save_result(self, analysis_id: str, df: pd.DataFrame, **extras: Any) -> None:
        payload = pickle.dumps({"df": df, "extras": extras}, protocol=pickle.HIGHEST_PROTOCOL)
        with self._lock:
            _atomic_write(self.dir(analysis_id) / "analysis.pkl", payload)
            self._bundles.pop(analysis_id, None)

    def load(self, analysis_id: str) -> AnalysisBundle | None:
        """Результат анализа (кэшируется до изменения файла). Только собственные файлы приложения: pickle не из внешних источников."""
        path = self.dir(analysis_id) / "analysis.pkl"
        meta = self.get_meta(analysis_id)
        if meta is None or not path.exists():
            return None
        mtime = path.stat().st_mtime
        with self._lock:
            cached = self._bundles.get(analysis_id)
            if cached and cached[0] == mtime:
                return AnalysisBundle(meta=meta, df=cached[1].df, extras=cached[1].extras)
        try:
            data = pickle.loads(path.read_bytes())  # noqa: S301
        except Exception:  # noqa: BLE001
            return None
        bundle = AnalysisBundle(meta=meta, df=data["df"], extras=data.get("extras", {}))
        with self._lock:
            self._bundles[analysis_id] = (mtime, bundle)
            if len(self._bundles) > 6:
                self._bundles.pop(next(iter(self._bundles)))
        return bundle

    def delete(self, analysis_id: str) -> None:
        with self._lock:
            self._bundles.pop(analysis_id, None)
            shutil.rmtree(self.dir(analysis_id), ignore_errors=True)

    # ── решения сотрудника по операциям ──

    def load_reviews(self, analysis_id: str) -> dict[str, dict[str, Any]]:
        try:
            return json.loads((self.dir(analysis_id) / "reviews.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def update_review(self, analysis_id: str, idx: str, **fields: Any) -> dict[str, Any]:
        """Правки сотрудника по операции: decision, comment, checked, in_report, recommendation (отредактированный текст).
        Переданные поля меняются, остальные сохраняются; запись без содержимого удаляется."""
        if "decision" in fields and fields["decision"] not in REVIEW_DECISIONS:
            raise ValueError(f"Неизвестное решение: {fields['decision']!r}")
        with self._lock:
            reviews = self.load_reviews(analysis_id)
            entry = {"decision": "", "comment": "", "checked": [], "in_report": False, "recommendation": "",
                     **reviews.get(str(idx), {})}
            for key, value in fields.items():
                entry[key] = value.strip() if isinstance(value, str) else value
            if not (entry["decision"] or entry["comment"] or entry["checked"] or entry["in_report"] or entry["recommendation"]):
                reviews.pop(str(idx), None)
            else:
                entry["updated_at"] = _now()
                reviews[str(idx)] = entry
            _atomic_write(self.dir(analysis_id) / "reviews.json", json.dumps(reviews, ensure_ascii=False, indent=1).encode("utf-8"))
            return reviews.get(str(idx), {})

    def save_review(self, analysis_id: str, idx: str, decision: str, comment: str, checked: list[str],
                    in_report: bool | None = None, recommendation: str | None = None) -> dict[str, Any]:
        """Решение, комментарий и отметки сотрудника; in_report / recommendation = None — оставить прежнее значение."""
        fields: dict[str, Any] = {"decision": decision, "comment": comment, "checked": checked}
        if in_report is not None:
            fields["in_report"] = bool(in_report)
        if recommendation is not None:
            fields["recommendation"] = recommendation
        return self.update_review(analysis_id, idx, **fields)

    def set_in_report(self, analysis_id: str, idx: str, flag: bool) -> dict[str, Any]:
        return self.update_review(analysis_id, idx, in_report=bool(flag))

    def set_recommendation(self, analysis_id: str, idx: str, text: str, original: str) -> dict[str, Any]:
        """Отредактированная рекомендация; текст, совпадающий с исходным или пустой, снимает правку."""
        text = (text or "").strip()
        return self.update_review(analysis_id, idx, recommendation="" if text == (original or "").strip() else text)


STORE = AnalysisStore()
