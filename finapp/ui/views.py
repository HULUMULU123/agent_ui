"""Содержимое страниц: из результата анализа — HTML-блоки и таблицы для компонентов Gradio. Без обращения к Gradio."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from ..agent.prompts import ROUTE_CHECKS
from ..analytics import (LEVEL_LABELS, OperationFilters, STATUS_LABELS, STATUS_ORDER, amount_distribution, apply_filters,
                         category_breakdown, category_options, counterparty_table, criteria_lists, fmt_date, fmt_int,
                         fmt_money, fmt_pct, ground_options, grounds_summary, kpis, monthly_flow, related_operations,
                         safe_float, short_name, top_counterparties)
from ..store import REVIEW_DECISIONS, AnalysisBundle
from . import render as R

STATUS_COLUMN = "Статус"
OPS_COLUMNS = ["№", STATUS_COLUMN, "Дата", "Контрагент", "ИНН", "Назначение платежа", "Направление", "Сумма, ₽", "Категория",
               "Основание проверки", "Решение", "Рекомендация"]
PAGE_SIZES = ["25", "50", "100", "200"]

STATUS_MARK = {STATUS_LABELS["disputable"]: "🔴", STATUS_LABELS["attention"]: "🟠", STATUS_LABELS["unclassified"]: "⚪", STATUS_LABELS["typical"]: "🟢"}
LEVEL_MARK = {LEVEL_LABELS["high"]: "🔴", LEVEL_LABELS["check"]: "🟠", LEVEL_LABELS["low"]: "🟢"}


def mark_status(label: str) -> str:
    """Цветной маркер в тексте ячейки вместо pandas Styler: стили на каждую ячейку заметно утяжеляют таблицу Gradio."""
    return f"{STATUS_MARK.get(label, '')} {label}".strip()


def styled(frame: pd.DataFrame, column: str = STATUS_COLUMN) -> pd.DataFrame:
    return frame


@dataclass
class Context:
    """Всё, что нужно страницам: результат анализа + решения сотрудника."""

    bundle: AnalysisBundle
    reviews: dict[str, dict]
    risk_db: Any | None = None

    @property
    def df(self) -> pd.DataFrame:
        return self.bundle.df

    @property
    def meta(self) -> dict:
        return self.bundle.meta

    @property
    def cp_table(self) -> pd.DataFrame:
        cache = self.bundle.extras.setdefault("_cp_table", None)
        if cache is None:
            cache = counterparty_table(self.df, self.risk_db)
            self.bundle.extras["_cp_table"] = cache
        return cache


# ───────────────────────── Верхняя панель ─────────────────────────

def analysis_label(meta: dict) -> str:
    name = meta.get("org_name") or "Организация не определена"
    return f"{name} · {meta.get('filename', '')}"


def period_html(meta: dict | None) -> str:
    if not meta:
        return '<div class="period-box">Выберите анализ</div>'
    period = f"{meta.get('period_start') or '—'} — {meta.get('period_end') or '—'}"
    return (f'<div class="period-box">Период: <b>{R.esc(period)}</b> · дата заявления: <b>{R.esc(meta.get("filing_date") or "—")}</b></div>')


# ───────────────────────── Реестр ─────────────────────────

def registry_kpis(metas: list[dict]) -> str:
    done = [m for m in metas if m["status"] == "done"]
    total_ops = sum(m.get("n_ops") or 0 for m in done)
    risky = sum(m.get("n_risky") or 0 for m in done)
    attention = sum(1 for m in metas if m["status"] == "error" or (m["status"] == "done" and (m.get("n_attention") or 0) > 0))
    return R.kpi_row([
        R.kpi_card("Всего анализов", fmt_int(len(metas)), "file", "blue"),
        R.kpi_card("Всего операций", fmt_int(total_ops), "check", "green"),
        R.kpi_card("Потенциально оспоримые", fmt_int(risky), "alert", "red", hint="Операции, отмеченные агентом для приоритетной проверки"),
        R.kpi_card("Требуют внимания", fmt_int(attention), "search", "amber", hint="Анализы с ошибкой или операциями, результат по которым ненадёжен"),
    ])


REGISTRY_COLUMNS = ["Код", "Организация", "ИНН", "Файл", "Период", "Операций", "Потенциально оспоримые", "Не классифицировано",
                    "Загружено", "Статус"]


def registry_table(metas: list[dict]) -> pd.DataFrame:
    rows = []
    for m in metas:
        label, _ = R.JOB_STATUS.get(m["status"], (m["status"], ""))
        if m["status"] == "done" and (m.get("n_attention") or 0) > 0:
            label = "Требует внимания"
        elif m["status"] in ("queued", "parsing", "analyzing"):
            label = f"{label} · {int(100 * safe_float(m.get('progress')))}%"
        rows.append({
            "Код": m["id"][-6:], "Организация": m.get("org_name") or "—", "ИНН": m.get("org_inn") or "—", "Файл": m["filename"],
            "Период": f"{m.get('period_start') or '—'} – {m.get('period_end') or '—'}",
            "Операций": fmt_int(m.get("n_ops") or 0) if m["status"] == "done" else "—",
            "Потенциально оспоримые": fmt_int(m.get("n_risky") or 0) if m["status"] == "done" else "—",
            "Не классифицировано": fmt_int(m.get("n_unclassified") or 0) if m["status"] == "done" else "—",
            "Загружено": R.fmt_stamp(m.get("created_at")), "Статус": label})
    return pd.DataFrame(rows, columns=REGISTRY_COLUMNS)


def registry_styled(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame
    marks = (("Анализ завершён", "🟢"), ("Ошибка", "🔴"), ("Остановлен", "🔴"), ("Требует", "🟠"))
    frame = frame.copy()
    frame["Статус"] = [next((f"{m} {v}" for key, m in marks if v.startswith(key)), f"🔵 {v}") for v in frame["Статус"]]
    return frame


def meta_by_code(metas: list[dict], code: str) -> dict | None:
    return next((m for m in metas if m["id"][-6:] == str(code)), None)


# ───────────────────────── Обзор ─────────────────────────

def overview_head(ctx: Context) -> str:
    meta = ctx.meta
    sub = f"{meta.get('org_name') or 'Организация не определена'} · выписка «{meta.get('filename', '')}» · дата заявления {meta.get('filing_date', '')}"
    mode_note = ""
    if meta.get("mode") == "rules":
        mode_note = ('<div class="note warn">Анализ выполнен в режиме предварительной оценки по правилам: GigaChat не был подключён, '
                     'связи сторон, профиль контрагента и судебная практика не использовались. Подключите GigaChat на странице «Настройки» '
                     'и загрузите выписку повторно для полного анализа.</div>')
    return R.page_header("Анализ операций по счетам", sub) + R.disclaimer() + mode_note


def overview_kpis(ctx: Context) -> str:
    df, k = ctx.df, kpis(ctx.df)
    total = max(k["total"], 1)
    s = lambda status: R.month_series(df, df["status"] == status)  # noqa: E731
    return R.kpi_row([
        R.kpi_card("Всего операций", fmt_int(k["total"]), "file", "blue", spark=R.sparkline(R.month_series(df), "#3ac3ff")),
        R.kpi_card("Классифицировано", fmt_int(k["recognized"]), "check", "green", sub=fmt_pct(k["recognized"], total),
                   spark=R.sparkline(R.month_series(df, df["status"] != "unclassified"), "#25e3a5")),
        R.kpi_card("Потенциально оспоримые", fmt_int(k["disputable"]), "alert", "red", sub=fmt_pct(k["disputable"], total),
                   spark=R.sparkline(s("disputable"), "#ff6b6b"), hint="Операции, отмеченные агентом для приоритетной проверки"),
        R.kpi_card("Требуют проверки", fmt_int(k["attention"]), "search", "amber", sub=fmt_pct(k["attention"], total),
                   spark=R.sparkline(s("attention"), "#f5b83d"), hint="Автоматический результат ненадёжен"),
        R.kpi_card("Не классифицировано", fmt_int(k["unclassified"]), "help", "gray", sub=fmt_pct(k["unclassified"], total),
                   spark=R.sparkline(s("unclassified"), "#8b98a9")),
    ])


def overview_cards(ctx: Context, top_mode: str = "count") -> dict[str, str]:
    df, k = ctx.df, kpis(ctx.df)
    cp = ctx.cp_table
    by_count = top_mode == "count"
    top = top_counterparties(cp, "count" if by_count else "turnover", 5)
    value_fmt = (lambda v: fmt_int(v)) if by_count else (lambda v: fmt_money(v, True))
    return {
        "cash": R.card("Движение денежных средств", R.cashflow_card(k), "coins"),
        "month": R.card("Динамика движения средств по месяцам", R.grouped_bars(monthly_flow(df)), "chart"),
        "cat": R.card("Операции по категориям", R.categories_donut(category_breakdown(df)), "list"),
        "dist": R.card("Распределение операций по суммам", R.column_chart(amount_distribution(df)), "chart"),
        "top": R.card("Топ-5 контрагентов", R.hbars(top, value_fmt), "users"),
    }


# ───────────────────────── Операции ─────────────────────────

def operations_kpis(ctx: Context) -> str:
    k = kpis(ctx.df)
    total = max(k["total"], 1)
    return R.kpi_row([
        R.kpi_card("Всего операций", fmt_int(k["total"]), "file", "blue"),
        R.kpi_card("Потенциально оспоримые", fmt_int(k["disputable"]), "alert", "red", sub=fmt_pct(k["disputable"], total)),
        R.kpi_card("Требуют проверки", fmt_int(k["attention"]), "search", "amber", sub=fmt_pct(k["attention"], total)),
        R.kpi_card("Не классифицированы", fmt_int(k["unclassified"]), "help", "gray", sub=fmt_pct(k["unclassified"], total)),
    ])


def to_number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(str(value).replace(" ", "").replace(" ", "").replace(",", "."))
    except ValueError:
        return None


def make_filters(query: str, direction: str, category: str, status: str, ground: str, amount_min: Any, amount_max: Any,
                 sort: str, only_marked: bool = False) -> OperationFilters:
    dir_map = {"Списание": "убытие", "Поступление": "прибытие"}
    return OperationFilters(query=query or "", direction=dir_map.get(direction, "Все"), category=category or "Все",
                            status=status or "Все", ground=ground or "Все", amount_min=to_number(amount_min),
                            amount_max=to_number(amount_max), sort=sort or "Дата ↑", only_marked=only_marked)


def operations_page(ctx: Context, filters: OperationFilters, page: int, page_size: int) -> tuple[pd.DataFrame, str, int, pd.DataFrame]:
    """-> (таблица текущей страницы, строка «показано», число страниц, отфильтрованные операции)."""
    filtered = apply_filters(ctx.df, filters)
    total = len(filtered)
    pages = max(1, -(-total // page_size))
    page = min(max(page, 0), pages - 1)
    chunk = filtered.iloc[page * page_size:(page + 1) * page_size]
    rows = []
    for position, (_, r) in enumerate(chunk.iterrows(), start=page * page_size + 1):
        review = ctx.reviews.get(str(r["idx"]), {})
        rows.append({
            "№": position, STATUS_COLUMN: mark_status(r["status_label"]), "Дата": fmt_date(r["date"]), "Контрагент": r["cp_name"] or "—",
            "ИНН": r["cp_inn"] or "—", "Назначение платежа": short_name(r["purpose"], 100),
            "Направление": "Списание" if r["direction"] == "убытие" else "Поступление" if r["direction"] == "прибытие" else "—",
            "Сумма, ₽": fmt_int(r["amount_abs"]), "Категория": r["operation_category"], "Основание проверки": r["ground"] or "—",
            "Решение": REVIEW_DECISIONS.get(review.get("decision", ""), "") if review else "",
            "Рекомендация": "✎ изменена" if review.get("recommendation") else ""})
    shown = f"Показано {fmt_int(len(chunk))} из {fmt_int(total)} операций · страница {page + 1} из {pages}"
    return pd.DataFrame(rows, columns=OPS_COLUMNS), shown, pages, filtered


def filter_choices(ctx: Context) -> dict[str, list[str]]:
    return {"category": category_options(ctx.df), "ground": ground_options(ctx.df)}


# ───────────────────────── Рекомендации (правятся сотрудником) ─────────────────────────

def original_recommendation(row: dict) -> str:
    return str(row.get("recommendation") or "")


def effective_recommendation(row: dict, review: dict | None) -> str:
    """Рекомендация, которую видит и выгружает сотрудник: его правка, если есть, иначе текст агента."""
    edited = (review or {}).get("recommendation")
    return edited if edited else original_recommendation(row)


REC_COLUMNS = ["№", STATUS_COLUMN, "Дата", "Контрагент", "Сумма, ₽", "Основание проверки", "Рекомендация (можно править)"]
REC_SCOPES = ["Отмеченные для проверки", "Только потенциально оспоримые", "Только требуют проверки", "Все операции", "Только с правками"]
REC_SIZES = ["15", "30", "50"]


def recommendations_filtered(ctx: Context, scope: str, query: str) -> pd.DataFrame:
    df = ctx.df
    if scope == "Только потенциально оспоримые":
        df = df[df["status"] == "disputable"]
    elif scope == "Только требуют проверки":
        df = df[df["status"] == "attention"]
    elif scope == "Только с правками":
        df = df[df["idx"].astype(str).isin([i for i, r in ctx.reviews.items() if r.get("recommendation")])]
    elif scope != "Все операции":
        df = df[df["status"].isin(["disputable", "attention"])]
    query = (query or "").strip().lower()
    if query:
        hay = (df["cp_name"].fillna("") + " " + df["cp_inn"].fillna("") + " " + df["purpose"].fillna("")).str.lower()
        df = df[hay.str.contains(query, regex=False)]
    order = {s: i for i, s in enumerate(STATUS_ORDER)}
    return df.assign(_p=df["status"].map(order)).sort_values(["_p", "amount_abs"], ascending=[True, False], kind="mergesort").drop(columns="_p")


def recommendations_page(ctx: Context, scope: str, query: str, page: int, size: int) -> tuple[pd.DataFrame, str, int]:
    filtered = recommendations_filtered(ctx, scope, query)
    pages = max(1, -(-len(filtered) // size))
    page = min(max(page, 0), pages - 1)
    chunk = filtered.iloc[page * size:(page + 1) * size]
    rows = []
    for position, (_, r) in enumerate(chunk.iterrows(), start=page * size + 1):
        review = ctx.reviews.get(str(r["idx"]), {})
        rows.append({"№": position, STATUS_COLUMN: mark_status(r["status_label"]), "Дата": fmt_date(r["date"]), "Контрагент": r["cp_name"] or "—",
                     "Сумма, ₽": fmt_int(r["amount_abs"]), "Основание проверки": r["ground"] or "—",
                     "Рекомендация (можно править)": effective_recommendation(r.to_dict(), review)})
    edited = sum(1 for r in ctx.reviews.values() if r.get("recommendation"))
    info = (f"Показано {fmt_int(len(chunk))} из {fmt_int(len(filtered))} операций · страница {page + 1} из {pages} · "
            f"изменено сотрудником: {fmt_int(edited)}")
    return pd.DataFrame(rows, columns=REC_COLUMNS), info, pages


def save_recommendation_edits(ctx: Context, scope: str, query: str, page: int, size: int, table: pd.DataFrame, store: Any) -> tuple[int, int]:
    """Сохраняет изменённые ячейки «Рекомендация» из таблицы страницы. Строки сопоставляются по колонке «№» (позиция в
    отфильтрованном списке), поэтому сортировка таблицы в браузере не мешает. -> (сохранено, сброшено к исходному)."""
    filtered = recommendations_filtered(ctx, scope, query)
    saved = reset = 0
    for _, row in table.iterrows():
        try:
            position = int(row["№"])
        except (TypeError, ValueError):
            continue
        if not 1 <= position <= len(filtered):
            continue
        source = filtered.iloc[position - 1]
        idx = str(source["idx"])
        current = effective_recommendation(source.to_dict(), ctx.reviews.get(idx))
        new = "" if pd.isna(row["Рекомендация (можно править)"]) else str(row["Рекомендация (можно править)"])
        if new.strip() == current.strip():
            continue
        original = original_recommendation(source.to_dict())
        store.set_recommendation(ctx.meta["id"], idx, new, original)
        if new.strip() in ("", original.strip()):
            reset += 1
        else:
            saved += 1
    return saved, reset


# ───────────────────────── Карточка операции ─────────────────────────

def detail_head(row: dict) -> str:
    crumbs = '<div class="crumbs">Операции / Детали операции</div>'
    return R.page_header("Детали операции", f"{fmt_date(row.get('date'))} · {row.get('cp_name') or 'контрагент не определён'}", crumbs)


def detail_blocks(ctx: Context, idx: str) -> dict[str, Any] | None:
    found = ctx.df[ctx.df["idx"].astype(str) == str(idx)]
    if found.empty:
        return None
    row = found.iloc[0].to_dict()
    review = ctx.reviews.get(str(idx), {})
    matched, missing, readiness = criteria_lists(row)
    related = related_operations(ctx.df, str(row["idx"]))
    return {
        "row": row, "review": review,
        "head": detail_head(row), "info": R.operation_info_card(row, review), "ground": R.ground_card(row),
        "docs": R.documents_card(row), "related": R.related_card(related),
        "missing": missing, "matched": matched, "readiness": readiness,
        "checked": [c for c in review.get("checked", []) if c in missing],
        "decision": review.get("decision", ""), "comment": review.get("comment", ""),
        "in_report": bool(review.get("in_report")),
        "recommendation": effective_recommendation(row, review), "recommendation_original": original_recommendation(row),
        "rec_edited": bool(review.get("recommendation")),
    }


# ───────────────────────── Контрагенты ─────────────────────────

CP_COLUMNS = ["ИНН", "Контрагент", "Операций", "Оборот, ₽", "Доля в обороте", "Основные категории", "Потенциально оспоримых",
              "Требуют проверки", "История", "Статус риска"]
CP_LEVEL_CHOICES = ["Все", *LEVEL_LABELS.values()]


def counterparties_kpis(ctx: Context) -> str:
    cp = ctx.cp_table
    if cp.empty:
        return R.kpi_row([R.kpi_card("Контрагентов с ИНН", "0", "users", "blue")])
    n_unknown = int(((ctx.df["cp_inn"] == "")).sum())
    return R.kpi_row([
        R.kpi_card("Всего контрагентов", fmt_int(len(cp)), "users", "blue"),
        R.kpi_card("Высокий риск", fmt_int((cp["level"] == "high").sum()), "alert", "red"),
        R.kpi_card("Требуют проверки", fmt_int((cp["level"] == "check").sum()), "search", "amber"),
        R.kpi_card("Операций без ИНН контрагента", fmt_int(n_unknown), "help", "gray"),
    ])


def counterparties_cards(ctx: Context, top_mode: str = "count") -> dict[str, str]:
    cp = ctx.cp_table
    top = top_counterparties(cp, "count" if top_mode == "count" else "turnover", 5)
    value_fmt = (lambda v: fmt_int(v)) if top_mode == "count" else (lambda v: fmt_money(v, True))
    if cp.empty:
        cat_html = '<p class="muted">Нет контрагентов с ИНН</p>'
    else:
        main_cat = cp["categories"].map(lambda c: c[0] if c else "Прочее")
        table = main_cat.value_counts().rename_axis("operation_category").reset_index(name="n")
        cat_html = R.categories_donut(table, "контрагентов")
    return {"top": R.card("Топ-5 контрагентов", R.hbars(top, value_fmt), "users"),
            "cat": R.card("Распределение контрагентов по основной категории операций", cat_html, "list")}


def counterparties_table(ctx: Context, query: str, level: str) -> pd.DataFrame:
    cp = ctx.cp_table
    if cp.empty:
        return pd.DataFrame(columns=CP_COLUMNS)
    query = (query or "").strip().lower()
    if query:
        cp = cp[(cp["name"].str.lower().str.contains(query, regex=False)) | (cp["inn"].str.contains(query, regex=False))]
    wanted = next((k for k, v in LEVEL_LABELS.items() if v == level), None)
    if wanted:
        cp = cp[cp["level"] == wanted]
    return pd.DataFrame({
        "ИНН": cp["inn"], "Контрагент": cp["name"], "Операций": cp["n_ops"].map(fmt_int), "Оборот, ₽": cp["turnover"].map(fmt_int),
        "Доля в обороте": cp.apply(lambda r: fmt_pct(r["share"], 1), axis=1), "Основные категории": cp["categories"].map(lambda c: "; ".join(c)),
        "Потенциально оспоримых": cp["n_disputable"], "Требуют проверки": cp["n_attention"], "История": cp["history_grade"],
        "Статус риска": cp["level"].map(LEVEL_LABELS)}, columns=CP_COLUMNS).reset_index(drop=True)


def cp_styled(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame
    frame = frame.copy()
    frame["Статус риска"] = [f"{LEVEL_MARK.get(v, '')} {v}".strip() for v in frame["Статус риска"]]
    return frame


def counterparty_detail(ctx: Context, inn: str) -> str:
    cp = ctx.cp_table
    found = cp[cp["inn"] == inn] if not cp.empty else cp
    if found.empty:
        return ""
    return R.counterparty_detail_html(found.iloc[0], ctx.df[ctx.df["cp_inn"] == inn])


# ───────────────────────── Основания проверки и сведения об анализе (внизу «Статистики») ─────────────────────────

def grounds_block(ctx: Context) -> str:
    summary = grounds_summary(ctx.df)
    if summary.empty:
        return ""
    return R.route_cards(summary, ROUTE_CHECKS)


def quality_block(ctx: Context) -> str:
    return R.parse_quality_html(ctx.bundle.extras.get("parse_summary") or {}, ctx.bundle.extras.get("notes") or [],
                                ctx.bundle.extras.get("tokens") or {}, ctx.meta.get("mode", ""))


# ───────────────────────── Отчёты: что сделал сотрудник ─────────────────────────

REPORT_COLUMNS = ["№", STATUS_COLUMN, "Дата", "Контрагент", "Назначение платежа", "Сумма, ₽", "Основание проверки", "Рекомендация", "Решение",
                  "Комментарий"]


def worked_frame(ctx: Context) -> pd.DataFrame:
    """Операции, по которым сотрудник что-то решил, написал или поправил в рекомендации."""
    ids = [i for i, r in ctx.reviews.items() if r.get("decision") or r.get("comment") or r.get("recommendation")]
    return ctx.df[ctx.df["idx"].astype(str).isin(ids)].sort_values("date")


def report_table(ctx: Context) -> pd.DataFrame:
    rows = []
    for n, (_, r) in enumerate(worked_frame(ctx).iterrows(), start=1):
        review = ctx.reviews.get(str(r["idx"]), {})
        rows.append({"№": n, STATUS_COLUMN: mark_status(r["status_label"]), "Дата": fmt_date(r["date"]), "Контрагент": r["cp_name"] or "—",
                     "Назначение платежа": short_name(r["purpose"], 120), "Сумма, ₽": fmt_int(r["amount_abs"]),
                     "Основание проверки": r["ground"] or "—",
                     "Рекомендация": ("✎ " if review.get("recommendation") else "") + short_name(effective_recommendation(r.to_dict(), review), 160),
                     "Решение": REVIEW_DECISIONS.get(review.get("decision", ""), ""), "Комментарий": short_name(review.get("comment", ""), 80)})
    return pd.DataFrame(rows, columns=REPORT_COLUMNS)


def report_kpis(ctx: Context) -> str:
    k = kpis(ctx.df)
    marked = k["disputable"] + k["attention"]
    decided = sum(1 for r in ctx.reviews.values() if r.get("decision"))
    edited = sum(1 for r in ctx.reviews.values() if r.get("recommendation"))
    return R.kpi_row([
        R.kpi_card("Отмечено для проверки", fmt_int(marked), "alert", "red"),
        R.kpi_card("Решений сотрудника", fmt_int(decided), "check", "green", sub=f"из {fmt_int(marked)} отмеченных"),
        R.kpi_card("Рекомендаций изменено", fmt_int(edited), "edit", "blue"),
    ])
