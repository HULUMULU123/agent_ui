"""HTML/SVG-блоки интерфейса: карточки KPI, графики, карточка операции. Всё, что пришло из данных, экранируется."""
from __future__ import annotations

import math
from html import escape
from typing import Any

import pandas as pd

from ..analytics import (LEVEL_LABELS, STATUS_COLORS, STATUS_HINTS, STATUS_LABELS, factors_of, fmt_date, fmt_int, fmt_money, fmt_pct, safe_float, short_name)
from ..store import REVIEW_DECISIONS

ICONS = {
    "upload": '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/>',
    "file": '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/>',
    "home": '<path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><polyline points="9 22 9 12 15 12 15 22"/>',
    "list": '<line x1="8" y1="6" x2="21" y2="6"/><line x1="8" y1="12" x2="21" y2="12"/><line x1="8" y1="18" x2="21" y2="18"/><line x1="3" y1="6" x2="3.01" y2="6"/><line x1="3" y1="12" x2="3.01" y2="12"/><line x1="3" y1="18" x2="3.01" y2="18"/>',
    "users": '<path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/>',
    "scale": '<path d="M12 3v18"/><path d="M5 7h14"/><path d="M5 7l-3 7a3 3 0 0 0 6 0z"/><path d="M19 7l-3 7a3 3 0 0 0 6 0z"/><path d="M8 21h8"/>',
    "report": '<path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/><rect x="8" y="2" width="8" height="4" rx="1"/><line x1="9" y1="12" x2="15" y2="12"/><line x1="9" y1="16" x2="15" y2="16"/>',
    "gear": '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09a1.65 1.65 0 0 0-1-1.51 1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09a1.65 1.65 0 0 0 1.51-1 1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33h0a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51h0a1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82v0a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/>',
    "alert": '<path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/>',
    "check": '<path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/>',
    "search": '<circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>',
    "help": '<circle cx="12" cy="12" r="10"/><path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"/><line x1="12" y1="17" x2="12.01" y2="17"/>',
    "clock": '<circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/>',
    "chart": '<line x1="12" y1="20" x2="12" y2="10"/><line x1="18" y1="20" x2="18" y2="4"/><line x1="6" y1="20" x2="6" y2="16"/>',
    "down": '<line x1="12" y1="5" x2="12" y2="19"/><polyline points="19 12 12 19 5 12"/>',
    "up": '<line x1="12" y1="19" x2="12" y2="5"/><polyline points="5 12 12 5 19 12"/>',
    "equal": '<line x1="5" y1="9" x2="19" y2="9"/><line x1="5" y1="15" x2="19" y2="15"/>',
    "coins": '<ellipse cx="12" cy="6" rx="8" ry="3"/><path d="M4 6v6c0 1.7 3.6 3 8 3s8-1.3 8-3V6"/><path d="M4 12v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6"/>',
    "edit": '<path d="M12 20h9"/><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4z"/>',
    "x": '<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>',
    "link": '<path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>',
}

CATEGORY_COLORS = ["#25e3a5", "#3ac3ff", "#a78bfa", "#f5b83d", "#ff7ab6", "#8b98a9", "#6ee7f9", "#ff9f6b", "#c4f16b", "#7c8cff"]


def icon(name: str, size: int = 18, cls: str = "") -> str:
    return (f'<svg class="ic {cls}" width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
            f'stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{ICONS[name]}</svg>')


def esc(value: Any) -> str:
    return escape("" if value is None else str(value))


def multiline(value: Any) -> str:
    return esc(value).replace("\n", "<br>")


# ───────────────────────── Общие блоки ─────────────────────────

def page_header(title: str, subtitle: str = "", crumbs: str = "") -> str:
    return (f'<div class="page-head">{crumbs}<h1>{esc(title)}</h1>'
            + (f'<p class="muted">{esc(subtitle)}</p>' if subtitle else "") + "</div>")


def disclaimer() -> str:
    return ('<div class="note">Оценка предварительная: «потенциально оспоримая» означает приоритет ручной проверки специалистом, '
            'а не вывод о недействительности сделки. Итоговое решение принимает сотрудник.</div>')


def sparkline(values: list[float], color: str, width: int = 74, height: int = 26) -> str:
    if not values or max(values) <= 0:
        return ""
    peak = max(values)
    n = len(values)
    bar_w = width / (n * 1.6)
    gap = bar_w * 0.6
    bars = []
    for i, v in enumerate(values):
        h = max(2.0, (v / peak) * (height - 2))
        bars.append(f'<rect x="{i * (bar_w + gap):.1f}" y="{height - h:.1f}" width="{bar_w:.1f}" height="{h:.1f}" rx="1" fill="{color}" opacity="{0.45 + 0.55 * v / peak:.2f}"/>')
    return f'<svg class="spark" width="{width}" height="{height}" viewBox="0 0 {width} {height}" aria-hidden="true">{"".join(bars)}</svg>'


def kpi_card(label: str, value: str, icon_name: str, tone: str, sub: str = "", spark: str = "", hint: str = "") -> str:
    return (f'<div class="kpi tone-{tone}" title="{esc(hint)}"><div class="kpi-ic">{icon(icon_name, 22)}</div>'
            f'<div class="kpi-body"><div class="kpi-label">{esc(label)}</div><div class="kpi-value">{value}</div>'
            f'{f"<div class=kpi-sub>{sub}</div>" if sub else ""}</div><div class="kpi-spark">{spark}</div></div>')


def kpi_row(cards: list[str]) -> str:
    return f'<div class="kpi-row n{len(cards)}">{"".join(cards)}</div>'


def status_chip(status: str) -> str:
    return (f'<span class="chip chip-{status}" title="{esc(STATUS_HINTS.get(status, ""))}">'
            f'<span class="dot"></span>{esc(STATUS_LABELS.get(status, status))}</span>')


def card(title: str, body: str, icon_name: str | None = None, cls: str = "", right: str = "") -> str:
    head = (f'<div class="card-head">{icon(icon_name, 18) if icon_name else ""}<h3>{esc(title)}</h3>'
            f'<div class="card-right">{right}</div></div>')
    return f'<section class="card {cls}">{head}<div class="card-body">{body}</div></section>'


def empty_state(title: str, text: str, icon_name: str = "file") -> str:
    return (f'<div class="empty"><div class="empty-ic">{icon(icon_name, 34)}</div><h3>{esc(title)}</h3>'
            f'<p class="muted">{esc(text)}</p></div>')


# ───────────────────────── Графики ─────────────────────────

def donut(segments: list[tuple[str, float, str]], center_value: str, center_label: str, size: int = 168) -> str:
    total = sum(v for _, v, _ in segments) or 1.0
    r, stroke = 62, 22
    c = 2 * math.pi * r
    offset = 0.0
    arcs = []
    for label, value, color in segments:
        if value <= 0:
            continue
        length = c * value / total
        arcs.append(f'<circle cx="80" cy="80" r="{r}" fill="none" stroke="{color}" stroke-width="{stroke}" '
                    f'stroke-dasharray="{max(length - 1.5, 0.5):.2f} {c - max(length - 1.5, 0.5):.2f}" '
                    f'stroke-dashoffset="{-offset:.2f}" transform="rotate(-90 80 80)"><title>{esc(label)}: {fmt_pct(value, total)}</title></circle>')
        offset += length
    svg = (f'<svg class="donut" viewBox="0 0 160 160" width="{size}" height="{size}" role="img" aria-label="Диаграмма: {esc(center_label)}">'
           f'<circle cx="80" cy="80" r="{r}" fill="none" stroke="rgba(255,255,255,.06)" stroke-width="{stroke}"/>{"".join(arcs)}'
           f'<text x="80" y="78" text-anchor="middle" class="donut-v">{esc(center_value)}</text>'
           f'<text x="80" y="96" text-anchor="middle" class="donut-l">{esc(center_label)}</text></svg>')
    legend = "".join(
        f'<li><span class="sw" style="background:{color}"></span><span class="lg-name">{esc(label)}</span>'
        f'<span class="lg-n">{fmt_int(value)}</span><span class="lg-p">{fmt_pct(value, total)}</span></li>'
        for label, value, color in segments if value > 0)
    return f'<div class="donut-wrap">{svg}<ul class="legend">{legend}</ul></div>'


def categories_donut(table: pd.DataFrame, center_label: str = "операций", top: int = 6) -> str:
    if table.empty:
        return '<p class="muted">Нет данных</p>'
    head = table.head(top)
    segments = [(r["operation_category"], float(r["n"]), CATEGORY_COLORS[i % len(CATEGORY_COLORS)]) for i, r in head.iterrows()]
    rest = float(table["n"].iloc[top:].sum())
    if rest > 0:
        segments.append(("Остальные", rest, "#4a5b6c"))
    return donut(segments, fmt_int(table["n"].sum()), center_label)


def grouped_bars(months: pd.DataFrame) -> str:
    """Помесячные поступления и списания, млн ₽."""
    if months.empty:
        return '<p class="muted">Нет данных по датам</p>'
    inflow = (months["inflow"] / 1e6).tolist()
    outflow = (months["outflow"] / 1e6).tolist()
    peak = max(max(inflow, default=0), max(outflow, default=0), 0.001)
    step = 10 ** math.floor(math.log10(peak))
    for mult in (1, 2, 2.5, 5, 10):
        if peak / (step * mult) <= 5:
            step *= mult
            break
    top = math.ceil(peak / step) * step
    w, h, left, bottom = 640, 230, 38, 30
    plot_h, plot_w = h - bottom - 10, w - left - 8
    n = len(months)
    group_w = plot_w / n
    bar_w = min(group_w * 0.32, 16)
    grid = []
    ticks = int(round(top / step))
    for i in range(ticks + 1):
        v = i * step
        y = 10 + plot_h - plot_h * v / top
        label = f"{v:g}"
        grid.append(f'<line x1="{left}" x2="{w - 8}" y1="{y:.1f}" y2="{y:.1f}" class="gl"/><text x="{left - 6}" y="{y + 3:.1f}" text-anchor="end" class="ax">{label}</text>')
    bars = []
    label_every = max(1, math.ceil(n / 12))
    for i, (m, a, b) in enumerate(zip(months["month"], inflow, outflow)):
        cx = left + group_w * (i + 0.5)
        for dx, val, color, name in ((-bar_w / 2 - 1, a, "#25e3a5", "Поступления"), (bar_w / 2 + 1, b, "#ff7a8a", "Списания")):
            bh = plot_h * val / top
            bars.append(f'<rect x="{cx + dx - bar_w / 2:.1f}" y="{10 + plot_h - bh:.1f}" width="{bar_w:.1f}" height="{max(bh, 0):.1f}" rx="2" fill="{color}">'
                        f'<title>{esc(m)} — {name}: {val:.2f} млн ₽</title></rect>')
        if i % label_every == 0:
            y_, mo = m.split("-") if "-" in m else (m, "")
            bars.append(f'<text x="{cx:.1f}" y="{h - 12}" text-anchor="middle" class="ax">{mo}.{y_[2:]}</text>')
    return (f'<svg class="bars" viewBox="0 0 {w} {h}" role="img" aria-label="Динамика поступлений и списаний по месяцам, млн рублей">'
            f'{"".join(grid)}{"".join(bars)}</svg>'
            '<div class="legend-inline"><span><i class="sw" style="background:#25e3a5"></i>Поступления</span>'
            '<span><i class="sw" style="background:#ff7a8a"></i>Списания</span><span class="muted">млн ₽</span></div>')


def column_chart(items: list[tuple[str, int]]) -> str:
    peak = max((v for _, v in items), default=0) or 1
    cols = "".join(
        f'<div class="col"><div class="col-v">{fmt_int(v)}</div><div class="col-bar" style="height:{max(4, 130 * v / peak):.0f}px"></div>'
        f'<div class="col-l">{esc(label)}</div></div>' for label, v in items)
    return f'<div class="columns" role="img" aria-label="Распределение операций по суммам">{cols}</div>'


def hbars(items: list[dict], value_fmt) -> str:  # noqa: ANN001
    if not items:
        return '<p class="muted">Нет контрагентов с ИНН</p>'
    rows = "".join(
        f'<div class="hb"><span class="hb-n">{i}</span><span class="hb-name" title="{esc(it["name"])} · ИНН {esc(it["inn"])}">{esc(short_name(it["name"], 34))}</span>'
        f'<span class="hb-track"><span class="hb-fill" style="width:{max(3, 100 * it["bar"]):.0f}%"></span></span>'
        f'<span class="hb-v">{value_fmt(it["value"])}</span><span class="hb-p">{fmt_pct(it["share"], 1)}</span></div>'
        for i, it in enumerate(items, 1))
    return f'<div class="hbars">{rows}</div>'


# ───────────────────────── Страница «Обзор» ─────────────────────────

def cashflow_card(k: dict) -> str:
    net = k["net"]
    rows = [
        ("down", "Поступления", fmt_money(k["inflow"], True), "pos"),
        ("up", "Списания", fmt_money(k["outflow"], True), "neg"),
        ("equal", "Чистый поток", ("+" if net > 0 else "−" if net < 0 else "") + fmt_money(abs(net), True), "pos" if net >= 0 else "neg"),
        ("coins", "Объём потенциально оспоримых операций", fmt_money(k["risky_amount"], True), "neg"),
    ]
    body = "".join(f'<div class="cf-row tone-{t}"><span class="cf-ic">{icon(i, 16)}</span><span class="cf-l">{l}</span><span class="cf-v">{v}</span></div>'
                   for i, l, v, t in rows)
    return body


def month_series(df: pd.DataFrame, mask: pd.Series | None = None) -> list[float]:
    frame = df if mask is None else df[mask]
    valid = frame[frame["month"].ne("NaT")]
    if valid.empty:
        return []
    counts = valid.groupby("month").size()
    months = pd.period_range(df.loc[df["month"].ne("NaT"), "month"].min(), df.loc[df["month"].ne("NaT"), "month"].max(), freq="M").astype(str)
    return counts.reindex(months, fill_value=0).astype(float).tolist()


# ───────────────────────── Реестр анализов ─────────────────────────

JOB_STATUS = {
    "queued": ("В очереди", "queued"), "parsing": ("Распознаётся", "run"), "analyzing": ("Анализ", "run"),
    "done": ("Анализ завершён", "ok"), "error": ("Ошибка", "bad"), "cancelled": ("Остановлен", "bad"),
}


def job_chip(meta: dict) -> str:
    label, tone = JOB_STATUS.get(meta["status"], (meta["status"], "queued"))
    if meta["status"] == "done" and (meta.get("n_attention") or 0) > 0:
        label, tone = "Требует внимания", "warn"
    return f'<span class="chip chip-job-{tone}"><span class="dot"></span>{esc(label)}</span>'


def active_jobs_html(metas: list[dict]) -> str:
    active = [m for m in metas if m["status"] in ("queued", "parsing", "analyzing")]
    if not active:
        return ""
    items = []
    for m in active:
        pct = int(100 * safe_float(m.get("progress")))
        label, tone = JOB_STATUS.get(m["status"], ("", "run"))
        items.append(
            f'<div class="job"><div class="job-ic">{icon("file", 20)}</div><div class="job-main">'
            f'<div class="job-title">{esc(m["filename"])}<span class="muted"> · {esc(m.get("org_name") or "организация определяется")}</span></div>'
            f'<div class="job-msg">{esc(m.get("message") or "")}</div>'
            f'<div class="bar"><div class="bar-fill" style="width:{pct}%"></div></div></div>'
            f'<div class="job-side">{job_chip(m)}<span class="job-pct">{pct}%</span></div></div>')
    return card("Сейчас обрабатывается", "".join(items), "clock", "jobs")


def recent_uploads_html(metas: list[dict], limit: int = 3) -> str:
    if not metas:
        return ""
    items = []
    for m in metas[:limit]:
        pct = int(100 * safe_float(m.get("progress")))
        items.append(
            f'<div class="recent"><div class="job-ic">{icon("file", 18)}</div><div class="job-main">'
            f'<div class="job-title">{esc(m["filename"])}</div>'
            f'<div class="muted">{esc(m.get("org_name") or "—")} · {fmt_size(m.get("size", 0))} · {esc(fmt_stamp(m.get("created_at")))}</div>'
            f'</div><div class="job-side">{job_chip(m)}<div class="bar slim"><div class="bar-fill" style="width:{pct}%"></div></div></div></div>')
    return card("Последние загрузки", f'<div class="recent-grid">{"".join(items)}</div>', "clock", "recent")


def fmt_size(size: int) -> str:
    size = float(size or 0)
    for unit in ("Б", "КБ", "МБ", "ГБ"):
        if size < 1024 or unit == "ГБ":
            return f"{size:.0f} {unit}" if unit == "Б" else f"{size:.1f} {unit}".replace(".", ",")
        size /= 1024
    return ""


def fmt_stamp(value: Any) -> str:
    ts = pd.to_datetime(value, errors="coerce")
    return "—" if pd.isna(ts) else f"{ts:%d.%m.%Y %H:%M}"


# ───────────────────────── Таблица операций (простой HTML: быстро на слабых ноутбуках) ─────────────────────────

def ops_table_html(rows: list[dict]) -> str:
    """Таблица операций обычным HTML. Строка кликабельна: скрипт из ui.app.HEAD передаёт номер строки в скрытое поле Gradio."""
    if not rows:
        return empty_state("Операций не найдено", "Измените поиск или фильтры.", "search")
    head = ("<thead><tr><th>№</th><th>Риск (0/1)</th><th>Дата</th><th>Контрагент</th><th>Назначение платежа</th><th>Направление</th>"
            '<th class="num">Сумма, ₽</th><th>Тип операции</th></tr></thead>')
    body = "".join(
        f'<tr data-n="{r["n"]}" tabindex="0"><td class="n">{r["n"]}</td><td class="rk r{r["risk"]}">{r["risk"]}</td><td class="nw">{esc(r["date"])}</td>'
        f'<td class="cp" title="{esc(r["cp"])}">{esc(r["cp"])}</td><td class="pur" title="{esc(r["purpose"])}">{esc(r["purpose"])}</td>'
        f'<td class="nw">{esc(r["direction"])}</td><td class="num nw">{esc(r["amount"])}</td><td class="ot" title="{esc(r["optype"])}">{esc(r["optype"])}</td></tr>'
        for r in rows)
    return f'<div class="ops-wrap"><table class="ops">{head}<tbody>{body}</tbody></table></div>'


# ───────────────────────── Карточка операции ─────────────────────────

FACTOR_ICONS = ["clock", "coins", "link", "alert", "users", "scale"]


def operation_info_card(row: dict, review: dict | None) -> str:
    out = row["direction"] == "убытие"
    arrow = icon("up" if out else "down", 18)
    direction_label = "Списание" if out else "Поступление" if row["direction"] == "прибытие" else "Направление не определено"
    items = [
        ("Дата", fmt_date(row.get("date"))), ("Контрагент", row.get("cp_name") or "—"), ("ИНН", row.get("cp_inn") or "—"),
        ("Назначение платежа", row.get("purpose") or "—"), ("Категория", row.get("operation_category") or "—"),
        ("Тип операции", row.get("operation_type") or "—"), ("Период относительно заявления", row.get("period") or "—"),
    ]
    rows = "".join(f'<div class="kv"><span>{esc(k)}</span><b>{esc(v)}</b></div>' for k, v in items)
    decision = ""
    if review and review.get("decision"):
        decision = f'<div class="review-badge">Решение сотрудника: {esc(REVIEW_DECISIONS[review["decision"]])}</div>'
    body = (f'<div class="status-banner st-{row["status"]}">{icon("alert" if row["status"] == "disputable" else "check", 18)}'
            f'<span>Риск {"1" if row["status"] == "disputable" else "0"} — {esc(STATUS_LABELS[row["status"]]).lower()}</span></div>'
            f'<div class="amount {"neg" if out else "pos"}">{fmt_money(row["amount_abs"])}</div>'
            f'<div class="dir {"neg" if out else "pos"}">{arrow}<span>{direction_label}</span></div>{decision}'
            f'<div class="kvs">{rows}</div>')
    return card("Информация об операции", body, "file", "op-info")


def ground_card(row: dict) -> str:
    risk = row.get("risk_level") == 1
    ground = row.get("ground")
    route = row.get("route_title") or ""
    if risk:
        head = (f'<div class="ground-box"><div class="ground-k">{esc("Основание проверки")}</div>'
                f'<div class="ground-t">{esc(ground or route)}</div><div class="ground-s">{esc(route)}</div>'
                f'<p>{esc(row.get("risk_explanation") or "")}</p></div>')
    else:
        head = (f'<div class="ground-box calm"><div class="ground-k">Основание проверки</div>'
                f'<div class="ground-t">Не выявлено</div><div class="ground-s">{esc(route)}</div>'
                f'<p>{esc(row.get("risk_explanation") or row.get("decision_argumentation") or "")}</p></div>')
    factors = factors_of(row)
    factor_html = "".join(f'<div class="factor"><span class="f-ic">{icon(FACTOR_ICONS[i % len(FACTOR_ICONS)], 18)}</span><span>{esc(f)}</span></div>'
                          for i, f in enumerate(factors))
    parts = [head]
    if factor_html:
        parts.append(f'<h4>Факторы правового риска</h4><div class="factors">{factor_html}</div>')
    if row.get("decision_argumentation"):
        parts.append(f'<h4>Обоснование</h4><p>{multiline(row["decision_argumentation"])}</p>')
    for title, key in (("Правовые основания", "legal_basis"), ("Судебная практика", "court_basis")):
        value = row.get(key)
        if isinstance(value, (list, tuple)) and value:
            parts.append(f'<h4>{title}</h4>' + "".join(f'<blockquote>{multiline(v)}</blockquote>' for v in value))
    if row.get("connections_basis"):
        parts.append(f'<h4>Связи сторон</h4><p>{multiline(row["connections_basis"])}</p>')
    parts.append(f'<div class="src muted">Источник анализа: {esc(row.get("source_label") or "")}'
                 + (f" · уверенность переноса: {esc({'high': 'высокая', 'medium': 'средняя'}.get(row.get('propagation_confidence'), ''))}"
                    if row.get("analysis_source") == "propagated" else "") + "</div>")
    return card("Основание проверки", "".join(parts), "scale", "op-ground")


def documents_card(row: dict) -> str:
    docs = row.get("documents_to_request")
    docs = docs if isinstance(docs, (list, tuple)) and docs else row.get("requested_documents")
    docs = [d for d in (docs or []) if isinstance(d, str)]
    if not docs:
        return ""
    items = "".join(f"<li>{esc(d)}</li>" for d in docs)
    return card("Документы к запросу", f'<ul class="doclist">{items}</ul>', "report", "op-docs")


def status_dot(status: str) -> str:
    return f'<span class="sdot" style="background:{STATUS_COLORS.get(status, "#8b98a9")}" title="{esc(STATUS_LABELS.get(status, ""))}"></span>'


def related_card(related: pd.DataFrame) -> str:
    if related.empty:
        return card("Связанные операции", '<p class="muted">Других операций с этим контрагентом нет.</p>', "link", "op-related")
    rows = "".join(
        f'<tr><td>{status_dot(r["status"])}{fmt_date(r["date"])}</td><td class="pur">{esc(short_name(r["purpose"], 60))}</td>'
        f'<td class="num">{fmt_money(r["amount_abs"], True)}</td></tr>' for _, r in related.iterrows())
    return card("Связанные операции", f'<table class="mini"><thead><tr><th>Дата</th><th>Назначение</th><th>Сумма</th></tr></thead><tbody>{rows}</tbody></table>'
                '<p class="muted small">Цвет точки — статус операции; другие операции того же контрагента, отмеченные для проверки, показаны первыми.</p>',
                "link", "op-related")


# ───────────────────────── Контрагенты и основания ─────────────────────────

def level_chip(level: str) -> str:
    tone = {"high": "bad", "check": "warn", "low": "ok"}[level]
    return f'<span class="chip chip-job-{tone}"><span class="dot"></span>{esc(LEVEL_LABELS[level])}</span>'


def counterparty_detail_html(row: pd.Series, ops: pd.DataFrame) -> str:
    cats = ", ".join(row["categories"]) or "—"
    head = (f'<div class="cp-head"><div><h3>{esc(row["name"])}</h3><div class="muted">ИНН {esc(row["inn"])} · основные категории: {esc(cats)}</div></div>'
            f'<div>{level_chip(row["level"])}</div></div>')
    stats = "".join(f'<div class="stat"><span>{esc(k)}</span><b>{esc(v)}</b></div>' for k, v in [
        ("Операций", fmt_int(row["n_ops"])), ("Оборот", fmt_money(row["turnover"], True)),
        ("Поступления", fmt_money(row["inflow"], True)), ("Списания", fmt_money(row["outflow"], True)),
        ("Операций с риском 1", fmt_int(row["n_disputable"])), ("Без результата анализа", fmt_int(row["n_attention"])),
        ("История в памяти риска", row["history_grade"]),
        ("Период", f"{fmt_date(row['first'])} — {fmt_date(row['last'])}")])
    shown = ops.sort_values("date", ascending=False).head(8)
    table = "".join(
        f'<tr><td>{fmt_date(r["date"])}</td><td class="pur">{esc(short_name(r["purpose"], 60))}</td><td class="num">{fmt_money(r["amount_abs"])}</td>'
        f'<td>{status_chip(r["status"])}</td></tr>' for _, r in shown.iterrows())
    return card("Контрагент", f'{head}<div class="stats">{stats}</div>'
                f'<table class="mini"><thead><tr><th>Дата</th><th>Назначение</th><th>Сумма</th><th>Статус</th></tr></thead><tbody>{table}</tbody></table>'
                + (f'<p class="muted">Показаны последние {len(shown)} из {len(ops)} операций.</p>' if len(ops) > len(shown) else ""),
                "users", "cp-detail")


def route_cards(summary: pd.DataFrame, checks: dict) -> str:
    if summary.empty:
        return empty_state("Оснований для проверки не найдено", "По этому анализу нет операций, отмеченных для ручной проверки.", "check")
    out = []
    for _, r in summary.iterrows():
        spec = checks.get(r["route"], {})
        questions = "".join(f"<li>{esc(q)}</li>" for q in spec.get("questions", []))
        tops = ", ".join(esc(short_name(n, 32)) for n in r["top_counterparties"]) or "—"
        out.append(
            f'<section class="card route"><div class="route-top"><div><div class="ground-t sm">{esc(r["ground"])}</div><h3>{esc(r["title"])}</h3>'
            f'<div class="muted">{esc(r["label"])}</div></div><div class="route-nums"><b>{fmt_int(r["n"])}</b><span>операций</span>'
            f'<b>{fmt_money(r["amount"], True)}</b><span>на сумму</span></div></div>'
            f'<div class="muted small">Крупнейшие контрагенты: {tops}</div>'
            + (f'<details><summary>Что проверять</summary><p class="muted small">{esc(spec.get("applies", ""))}</p><ul class="doclist">{questions}</ul></details>' if questions else "")
            + "</section>")
    return f'<div class="routes">{"".join(out)}</div>'


def parse_quality_html(summary: dict, notes: list[str], tokens: dict, mode: str) -> str:
    if not summary:
        return ""
    status_tone = {"ok": "ok", "ready_table": "ok", "ok_unverified": "warn", "needs_review": "bad"}.get(summary.get("status"), "warn")
    checks = "".join(
        f'<tr><td>{esc(c.get("check"))}</td><td><span class="chip chip-job-{"ok" if c.get("status") == "pass" else "warn" if c.get("level") != "error" else "bad"}">'
        f'{"Пройдена" if c.get("status") == "pass" else "Замечание"}</span></td><td class="muted">{esc(c.get("detail"))}</td></tr>'
        for c in summary.get("checks", [])[:14])
    errors = "".join(f"<li>{esc(e)}</li>" for e in summary.get("errors", []))
    note_items = "".join(f"<li>{esc(n)}</li>" for n in (summary.get("notes", []) + notes)[:12])
    body = (f'<div class="kv"><span>Результат распознавания</span><b><span class="chip chip-job-{status_tone}"><span class="dot"></span>{esc(summary.get("status_label"))}</span></b></div>'
            f'<div class="kv"><span>Владелец выписки</span><b>{esc(summary.get("owner_name") or "не определён")} {esc(summary.get("owner_inn") or "")}</b></div>'
            f'<div class="kv"><span>Режим анализа</span><b>{"GigaChat-3-Ultra" if mode == "gigachat" else "Предварительная оценка по правилам (без GigaChat)"}</b></div>'
            + (f'<div class="kv"><span>Токенов GigaChat</span><b>{fmt_int(tokens.get("total", 0))}</b></div>' if tokens and tokens.get("total") else "")
            + (f'<h4>Требуют внимания</h4><ul class="doclist warn">{errors}</ul>' if errors else "")
            + (f'<h4>Примечания</h4><ul class="doclist">{note_items}</ul>' if note_items else "")
            + (f'<h4>Проверки распознавания</h4><table class="mini"><tbody>{checks}</tbody></table>' if checks else ""))
    return card("Сведения об анализе и качество распознавания", body, "check", "quality")
