"""Витрина результатов для интерфейса: статусы операций, KPI, динамика, категории, контрагенты, основания проверки."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from .agent.common import is_missing, resolve_counterparty_inn, resolve_counterparty_name
from .agent.prompts import ROUTES
from .agent.risk_memory import RISK_GRADE_LABELS

# ───────────────────────── Статусы ─────────────────────────

# Как в тетради агента: risk_level только 0 или 1 (1 — «Требует проверки», 0 — «Риска нет»).
STATUS_ORDER = ["disputable", "typical"]
STATUS_LABELS = {"disputable": "Требует проверки", "typical": "Риска нет"}
STATUS_HINTS = {
    "disputable": "Риск 1: по операции есть вопрос, который обычное объяснение не снимает — нужна ручная проверка.",
    "typical": "Риск 0: операция понятна и не вызывает вопросов по доступным данным.",
}
STATUS_COLORS = {"disputable": "#ff6b6b", "typical": "#2ee6a6"}
RISK_FILTER_CHOICES = ["Все", "Риск 1", "Риск 0"]

# Основание проверки для таблиц и карточек (короткая ссылка на норму по маршруту).
ROUTE_GROUND = {
    "preference": "ст. 61.3", "counter_performance": "п. 1 ст. 61.2", "asset_withdrawal": "п. 2 ст. 61.2",
    "transit": "п. 2 ст. 61.2, ст. 10 ГК РФ", "formal_deal": "ст. 170 ГК РФ", "ordinary": "ст. 61.4",
}
ROUTE_SHORT_TITLES = {
    "preference": "Предпочтение отдельному кредитору", "counter_performance": "Неравноценное встречное исполнение",
    "asset_withdrawal": "Выбытие средств без встречного предоставления", "transit": "Транзит и дробление платежей",
    "formal_deal": "Мнимая или притворная сделка", "ordinary": "Обычная хозяйственная деятельность",
    "documents": "Документальная проверка основания", "none": "Оснований для проверки не выявлено",
}
SOURCE_LABELS = {
    "llm": "Автоматический анализ", "llm_second_pass": "Автоматический анализ (повторно)",
    "propagated": "Перенесено с похожей операции", "none": "Не проанализировано",
    "rule": "Правило: вне периодов подозрительности", "rules": "Предварительная оценка по правилам",
    "manual_review_required": "Не удалось — нужна ручная проверка",
}
CONFIDENCE_LABELS = {"high": "Высокая", "medium": "Средняя", "low": "Низкая"}
TOOL_LABELS = {"search_practice_and_normative": "Поиск норм и судебной практики", "get_conterparty_risk": "История риска контрагента"}


def derive_status(row: dict) -> str:
    """Только по risk_level: 1 -> «Требует проверки», 0 -> «Риска нет» (нет значения — тоже на проверку)."""
    risk = row.get("risk_level")
    return "typical" if not is_missing(risk) and int(risk) == 0 else "disputable"


def refresh_status(df: pd.DataFrame) -> pd.DataFrame:
    """Пересчёт статусов у анализов, сохранённых прежней версией (там было четыре статуса)."""
    risk = pd.to_numeric(df["risk_level"], errors="coerce")
    df["status"] = risk.map(lambda v: "typical" if v == 0 else "disputable")
    df["status_label"] = df["status"].map(STATUS_LABELS)
    df["manual"] = df["analysis_source"].isin(["manual_review_required", "none"])
    df["type_unknown"] = df.get("operation_type_need_review", False).fillna(False).astype(bool) if "operation_type_need_review" in df.columns else False
    return df


def enrich(df: pd.DataFrame) -> pd.DataFrame:
    """Добавляет колонки витрины: контрагент, статус, основание, месяц, подписи."""
    out = df.copy()
    records = out.to_dict("records")
    out["cp_inn"] = [resolve_counterparty_inn(r) or "" for r in records]
    out["cp_name"] = [resolve_counterparty_name(r) or "" for r in records]
    out["amount_abs"] = pd.to_numeric(out["amount"], errors="coerce").abs()
    out["month"] = pd.to_datetime(out["date"], errors="coerce").dt.to_period("M").astype(str)
    out["status"] = [derive_status(r) for r in records]
    out["status_label"] = out["status"].map(STATUS_LABELS)
    out["manual"] = out["analysis_source"].isin(["manual_review_required", "none"])
    out["type_unknown"] = out["operation_type_need_review"].fillna(False).astype(bool) if "operation_type_need_review" in out.columns else False
    out["ground"] = [ROUTE_GROUND.get(r.get("check_route"), "") if r.get("risk_level") == 1 else "" for r in records]
    out["route_title"] = out["check_route"].map(ROUTE_SHORT_TITLES).fillna("")
    out["source_label"] = out["analysis_source"].map(SOURCE_LABELS).fillna(out["analysis_source"])
    if "operation_category" not in out.columns:
        out["operation_category"] = "Прочее"
    return out


# ───────────────────────── Форматирование ─────────────────────────

def fmt_int(value: float | int) -> str:
    return f"{int(round(float(value))):,}".replace(",", " ")


def fmt_money(value: float, short: bool = False) -> str:
    value = float(value)
    if short:
        if abs(value) >= 1e9:
            return f"{value / 1e9:.1f}".replace(".", ",") + " млрд ₽"
        if abs(value) >= 1e6:
            return f"{value / 1e6:.1f}".replace(".", ",") + " млн ₽"
        if abs(value) >= 1e3:
            return f"{value / 1e3:.1f}".replace(".", ",") + " тыс ₽"
    return fmt_int(value) + " ₽"


def fmt_pct(part: float, total: float, digits: int = 1) -> str:
    return f"{(100 * part / total if total else 0):.{digits}f}".replace(".", ",") + "%"


def fmt_date(value: Any) -> str:
    ts = pd.to_datetime(value, errors="coerce")
    return "—" if pd.isna(ts) else f"{ts:%d.%m.%Y}"


def short_name(name: str, limit: int = 44) -> str:
    name = re.sub(r"\s+", " ", str(name or "")).strip()
    return name if len(name) <= limit else name[:limit - 1] + "…"


# ───────────────────────── Агрегаты для страниц ─────────────────────────

def kpis(df: pd.DataFrame) -> dict[str, Any]:
    total = len(df)
    counts = df["status"].value_counts().to_dict()
    manual = int(df["manual"].sum()) if "manual" in df.columns else 0
    type_unknown = int(df["type_unknown"].sum()) if "type_unknown" in df.columns else 0
    recognized = total - type_unknown
    inflow = float(df.loc[df["direction"] == "прибытие", "amount_abs"].sum())
    outflow = float(df.loc[df["direction"] == "убытие", "amount_abs"].sum())
    risky = df[df["status"] == "disputable"]
    return {
        "total": total, "recognized": recognized, "disputable": counts.get("disputable", 0),
        "attention": manual, "unclassified": type_unknown,
        "typical": counts.get("typical", 0), "inflow": inflow, "outflow": outflow, "net": inflow - outflow,
        "risky_amount": float(risky["amount_abs"].sum()),
        "period_start": df["date"].min(), "period_end": df["date"].max(),
        "counterparties": int(df.loc[df["cp_inn"] != "", "cp_inn"].nunique()),
    }


def monthly_flow(df: pd.DataFrame) -> pd.DataFrame:
    """Помесячные поступления и списания (млн ₽) для графика динамики; пропуски месяцев заполняются нулями."""
    valid = df[df["month"].ne("NaT")]
    if valid.empty:
        return pd.DataFrame(columns=["month", "inflow", "outflow"])
    table = valid.pivot_table(index="month", columns="direction", values="amount_abs", aggfunc="sum", fill_value=0.0)
    months = pd.period_range(table.index.min(), table.index.max(), freq="M").astype(str)
    table = table.reindex(months, fill_value=0.0)
    return pd.DataFrame({"month": months, "inflow": table.get("прибытие", 0.0).to_numpy(),
                         "outflow": table.get("убытие", 0.0).to_numpy()})


def category_breakdown(df: pd.DataFrame) -> pd.DataFrame:
    table = df.groupby("operation_category").agg(n=("idx", "count"), amount=("amount_abs", "sum")).reset_index()
    table["share"] = table["n"] / max(table["n"].sum(), 1)
    return table.sort_values("n", ascending=False).reset_index(drop=True)


AMOUNT_BINS = [(0, 10_000, "< 10 тыс."), (10_000, 100_000, "10–100 тыс."), (100_000, 1_000_000, "100 тыс. – 1 млн"),
               (1_000_000, 10_000_000, "1–10 млн"), (10_000_000, float("inf"), "> 10 млн")]


def amount_distribution(df: pd.DataFrame) -> list[tuple[str, int]]:
    amounts = df["amount_abs"].fillna(0)
    return [(label, int(((amounts >= lo) & (amounts < hi)).sum())) for lo, hi, label in AMOUNT_BINS]


def counterparty_table(df: pd.DataFrame, risk_db: Any | None = None) -> pd.DataFrame:
    """Сводка по контрагентам (по ИНН): обороты, категории, число спорных операций, итоговый статус риска."""
    work = df[df["cp_inn"] != ""]
    if work.empty:
        return pd.DataFrame()
    total_turnover = float(work["amount_abs"].sum())
    rows = []
    for inn, group in work.groupby("cp_inn"):
        names = group["cp_name"][group["cp_name"] != ""]
        name = names.value_counts().index[0] if len(names) else "—"
        cats = group["operation_category"].value_counts().index[:2].tolist()
        n_disputable = int((group["status"] == "disputable").sum())
        n_attention = int(group["manual"].sum()) if "manual" in group.columns else 0
        grade, score = None, None
        if risk_db is not None:
            try:
                current = risk_db.compute_current_risk(inn)
                grade, score = current["risk_grade"], current["risk_score"]
            except Exception:  # noqa: BLE001
                pass
        if n_disputable or grade in ("HIGH", "CRITICAL"):
            level = "high"
        elif n_attention or grade == "MEDIUM":
            level = "check"
        else:
            level = "low"
        turnover = float(group["amount_abs"].sum())
        rows.append({
            "inn": inn, "name": name, "n_ops": len(group), "turnover": turnover, "share": turnover / total_turnover if total_turnover else 0,
            "inflow": float(group.loc[group["direction"] == "прибытие", "amount_abs"].sum()),
            "outflow": float(group.loc[group["direction"] == "убытие", "amount_abs"].sum()),
            "categories": cats, "n_disputable": n_disputable, "n_attention": n_attention,
            "n_unclassified": int(group["type_unknown"].sum()) if "type_unknown" in group.columns else 0,
            "level": level, "history_grade": RISK_GRADE_LABELS.get(grade or "NO_HISTORY", "нет истории"),
            "history_score": score,
            "first": group["date"].min(), "last": group["date"].max(),
        })
    return pd.DataFrame(rows).sort_values("turnover", ascending=False).reset_index(drop=True)


LEVEL_LABELS = {"high": "Высокий", "check": "Средний", "low": "Низкий"}


def grounds_summary(df: pd.DataFrame) -> pd.DataFrame:
    """Основания проверки: сколько операций и на какую сумму отмечено по каждому маршруту (только risk_level = 1)."""
    risky = df[df["risk_level"] == 1]
    if risky.empty:
        return pd.DataFrame()
    rows = []
    for route, group in risky.groupby("check_route"):
        label, article = ROUTES.get(route, (route, None))
        top = group.loc[group["cp_inn"] != ""].groupby("cp_name")["amount_abs"].sum().sort_values(ascending=False).head(3)
        rows.append({"route": route, "title": ROUTE_SHORT_TITLES.get(route, label), "label": label, "article": article or "",
                     "ground": ROUTE_GROUND.get(route, ""), "n": len(group), "amount": float(group["amount_abs"].sum()),
                     "top_counterparties": list(top.index)})
    return pd.DataFrame(rows).sort_values("n", ascending=False).reset_index(drop=True)


def top_counterparties(table: pd.DataFrame, by: str = "count", n: int = 5) -> list[dict]:
    if table.empty:
        return []
    key = "n_ops" if by == "count" else "turnover"
    top = table.sort_values(key, ascending=False).head(n)
    base = float(table[key].sum()) or 1.0
    peak = float(top[key].max()) or 1.0
    return [{"name": r["name"], "inn": r["inn"], "value": float(r[key]), "share": float(r[key]) / base,
             "bar": float(r[key]) / peak} for _, r in top.iterrows()]


# ───────────────────────── Фильтрация таблицы операций ─────────────────────────

@dataclass
class OperationFilters:
    query: str = ""
    direction: str = "Все"
    category: str = "Все"
    status: str = "Все"
    ground: str = "Все"
    amount_min: float | None = None
    amount_max: float | None = None
    date_from: str = ""
    date_to: str = ""
    only_marked: bool = False
    risk: int | None = None  # 1 / 0 / None — все
    sort: str = "Дата ↑"


SORTS = {
    "Дата ↑": ("date", True), "Дата ↓": ("date", False), "Сумма ↓": ("amount_abs", False), "Сумма ↑": ("amount_abs", True),
    "Приоритет": ("_priority", True),
}


def apply_filters(df: pd.DataFrame, f: OperationFilters, presorted_by_date: bool = False) -> pd.DataFrame:
    """Фильтры и сортировка. df — полная таблица или лёгкое представление (ui.views.Context.view): в нём есть готовое
    поле поиска _hay и порядок по дате (presorted_by_date=True), поэтому на больших выписках не копируется и не сортируется всё."""
    out = df
    query = (f.query or "").strip().lower()
    if query:
        if "_hay" in out.columns:
            haystack = out["_hay"]
        else:
            haystack = (out["cp_name"].fillna("") + " " + out["cp_inn"].fillna("") + " " + out["purpose"].fillna("") + " "
                        + out["operation_type"].fillna("")).str.lower()
        out = out[haystack.str.contains(query, regex=False)]
    if f.direction in ("убытие", "прибытие"):
        out = out[out["direction"] == f.direction]
    if f.category not in ("Все", "", None):
        out = out[out["operation_category"] == f.category]
    if f.status not in ("Все", "", None):
        wanted = next((k for k, v in STATUS_LABELS.items() if v == f.status), None)
        if wanted:
            out = out[out["status"] == wanted]
    if f.ground not in ("Все", "", None):
        out = out[out["route_title"] == f.ground]
    if f.amount_min not in (None, ""):
        out = out[out["amount_abs"] >= float(f.amount_min)]
    if f.amount_max not in (None, ""):
        out = out[out["amount_abs"] <= float(f.amount_max)]
    if f.date_from:
        out = out[pd.to_datetime(out["date"], errors="coerce") >= pd.to_datetime(f.date_from, dayfirst=True, errors="coerce")]
    if f.date_to:
        out = out[pd.to_datetime(out["date"], errors="coerce") <= pd.to_datetime(f.date_to, dayfirst=True, errors="coerce")]
    if f.risk in (0, 1):
        out = out[out["risk_level"] == f.risk]
    if f.only_marked:
        out = out[out["status"] == "disputable"]
    column, ascending = SORTS.get(f.sort, SORTS["Дата ↑"])
    if column == "_priority":
        order = {s: i for i, s in enumerate(STATUS_ORDER)}
        # сначала риск 1, внутри групп — по дате
        out = out.assign(_priority=out["status"].map(order)).sort_values(["_priority", "date"], kind="mergesort").drop(columns="_priority")
    elif column == "date" and ascending and presorted_by_date:
        pass  # порядок уже по дате
    else:
        out = out.sort_values(column, ascending=ascending, kind="mergesort", na_position="last")
    return out


def related_operations(df: pd.DataFrame, idx: str, limit: int = 5) -> pd.DataFrame:
    """Другие операции с тем же контрагентом (для карточки операции): сначала отмеченные для проверки, затем по дате."""
    row = df[df["idx"] == idx]
    if row.empty or not row.iloc[0]["cp_inn"]:
        return df.iloc[0:0]
    same = df[(df["cp_inn"] == row.iloc[0]["cp_inn"]) & (df["idx"] != idx)]
    order = {s: i for i, s in enumerate(STATUS_ORDER)}
    return same.assign(_p=same["status"].map(order)).sort_values(["_p", "date"], ascending=[True, False]).head(limit)


def category_options(df: pd.DataFrame) -> list[str]:
    return ["Все"] + sorted(df["operation_category"].dropna().unique().tolist())


def ground_options(df: pd.DataFrame) -> list[str]:
    risky = df[df["risk_level"] == 1]["route_title"].dropna().unique().tolist()
    return ["Все"] + sorted(t for t in risky if t)


def factors_of(row: dict) -> list[str]:
    value = row.get("risk_factors")
    return [str(v) for v in value] if isinstance(value, (list, tuple)) else []


def criteria_lists(row: dict) -> tuple[list[str], list[str], str]:
    """challenge_criteria -> (подтверждено, установить, готовность к оценке)."""
    text = row.get("challenge_criteria") or ""
    matched: list[str] = []
    missing: list[str] = []
    readiness = ""
    for line in str(text).split("\n"):
        if line.startswith("Подтверждено:"):
            matched = [s.strip() for s in line.split(":", 1)[1].split(";") if s.strip()]
        elif line.startswith("Установить:"):
            missing = [s.strip() for s in line.split(":", 1)[1].split(";") if s.strip()]
        elif line.startswith("Готовность к оценке:"):
            readiness = line.split(":", 1)[1].strip()
    return matched, missing, readiness


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return default if np.isnan(result) else result
