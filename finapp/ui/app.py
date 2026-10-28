"""Интерфейс сотрудника «Банкротный анализ» на Gradio: реестр анализов, обзор, операции, контрагенты, основания, отчёты, настройки."""
from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import gradio as gr
import pandas as pd

from ..agent.common import MODEL_NAME, parse_filing_date
from ..analytics import refresh_status
from ..config import DATA_DIR, SETTINGS, AppSettings
from ..export import export_to_temp
from ..factory import check_gigachat, get_risk_db, legal_sources_status
from ..jobs import JOBS
from ..parser.service import SUPPORTED_SUFFIXES
from ..store import ACTIVE_STATUSES, REVIEW_DECISIONS, STORE
from . import render as R
from . import views as V
from .theme import CSS, THEME

logger = logging.getLogger("finapp.ui")

NAV = [("registry", "Анализы выписок", "file"), ("overview", "Обзор", "home"), ("operations", "Операции", "list"),
       ("recs", "Рекомендации", "edit"), ("counterparties", "Контрагенты", "users"), ("grounds", "Основания оспаривания", "scale"),
       ("reports", "Отчёты", "report"), ("settings", "Настройки", "gear")]
NAV_HIGHLIGHT = {"detail": "operations"}
DECISION_CHOICES = [(label, key) for key, label in REVIEW_DECISIONS.items()]
TOP_MODES = [("По количеству операций", "count"), ("По сумме оборота", "turnover")]
NO_ANALYSIS = "Нет данных: выберите завершённый анализ в списке сверху или загрузите выписку на странице «Анализы выписок»."


# Клик по строке HTML-таблицы операций -> скрытое поле Gradio (#op-pick) и скрытая кнопка (#op-pick-btn).
HEAD = """<script>
(function () {
  function pick(n) {
    var box = document.querySelector('#op-pick textarea, #op-pick input'), btn = document.querySelector('#op-pick-btn');
    if (!box || !btn) return;
    var proto = box.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, 'value').set.call(box, n + ':' + Date.now());
    box.dispatchEvent(new Event('input', {bubbles: true}));
    setTimeout(function () { btn.click(); }, 30);
  }
  document.addEventListener('click', function (e) { var tr = e.target.closest && e.target.closest('tr[data-n]'); if (tr) pick(tr.dataset.n); });
  document.addEventListener('keydown', function (e) {
    if (e.key !== 'Enter') return;
    var tr = e.target.closest && e.target.closest('tr[data-n]'); if (tr) pick(tr.dataset.n);
  });
})();
</script>"""


def context_for(aid: str | None) -> V.Context | None:
    if not aid:
        return None
    bundle = STORE.load(aid)
    if bundle is None:
        return None
    if not bundle.extras.get("_status_v2"):  # анализы, сохранённые прежней версией (четыре статуса), приводим к риску 0/1
        refresh_status(bundle.df)
        bundle.extras["_status_v2"] = True
    return V.Context(bundle=bundle, reviews=STORE.load_reviews(aid), risk_db=get_risk_db())


def analysis_choices() -> list[tuple[str, str]]:
    return [(V.analysis_label(m), m["id"]) for m in STORE.list() if m["status"] == "done"]


def empty(title: str = "Нет данных", text: str = NO_ANALYSIS) -> str:
    return R.empty_state(title, text)


def build_app() -> gr.Blocks:
    STORE.mark_interrupted()
    C = SimpleNamespace()
    with gr.Blocks(title="Банкротный анализ", theme=THEME, css=CSS, head=HEAD, fill_width=True, analytics_enabled=False) as demo:
        aid = gr.State(None)
        sel_idx = gr.State(None)
        reg_code = gr.State(None)
        cp_inn = gr.State(None)
        poll_sig = gr.State(("", ""))
        op_page = gr.State(0)

        with gr.Row(elem_id="shell"):
            # ───── боковая панель ─────
            with gr.Column(elem_id="sidebar", scale=0):
                gr.HTML(f'<div class="brand"><div class="brand-logo">{R.icon("chart", 22)}</div>'
                        '<div><div class="brand-name">Банкротный<br>анализ</div><div class="brand-sub">анализ выписок</div></div></div>')
                C.nav = {}
                for key, label, ic in NAV:
                    C.nav[key] = gr.Button(label, elem_classes=["nav", "active"] if key == "registry" else ["nav"], key=f"nav-{key}")
                C.side_status = gr.HTML(elem_id="side-foot")

            # ───── основная область ─────
            with gr.Column(elem_id="main", scale=1):
                with gr.Row(elem_id="topbar"):
                    C.analysis_dd = gr.Dropdown(choices=analysis_choices(), value=None, label="Организация / выписка",
                                                scale=3, min_width=320, interactive=True, filterable=True, allow_custom_value=False)
                    with gr.Column(scale=2, min_width=260):
                        C.period = gr.HTML(V.period_html(None))

                with gr.Tabs(selected="registry", elem_id="pages") as pages:
                    # ═══════ Реестр ═══════
                    with gr.Tab("registry", id="registry"):
                        gr.HTML(R.page_header("Анализы выписок", "Реестр загруженных выписок: загрузите файл, следите за ходом анализа и открывайте готовые результаты"))
                        C.reg_kpis = gr.HTML()
                        with gr.Group(elem_classes=["ctrl-card", "upload-card"]):
                            gr.HTML('<h3>Загрузить выписку</h3>')
                            with gr.Row(elem_classes=["upload-grid"]):
                                with gr.Column(scale=3, min_width=320):
                                    gr.HTML('<div class="step"><span class="step-n">1</span>Перетащите файл в рамку или нажмите на неё</div>')
                                    C.up_files = gr.File(label="Файлы выписок", show_label=False, container=False, file_count="multiple",
                                                         file_types=list(SUPPORTED_SUFFIXES), height=190, elem_id="up-files")
                                    gr.HTML('<p class="muted small up-hint">Excel, CSV, Word, PDF, HTML или текст 1С. Можно выбрать сразу несколько файлов — '
                                            'они встанут в очередь. Таблицу операций и владельца счёта парсер определит сам.</p>')
                                with gr.Column(scale=2, min_width=280, elem_classes=["upload-side"]):
                                    gr.HTML('<div class="step"><span class="step-n">2</span>Укажите дату заявления о банкротстве</div>')
                                    C.up_date = gr.Textbox(label="Дата заявления (ДД.ММ.ГГГГ)", show_label=False, container=False,
                                                           placeholder="например, 10.10.2025")
                                    gr.HTML('<p class="muted small up-hint">От неё считаются периоды подозрительности: 1 месяц, 6 месяцев, 1 год, 3 года.</p>')
                                    gr.HTML('<div class="step"><span class="step-n">3</span>Запустите анализ</div>')
                                    C.up_btn = gr.Button("Начать анализ", variant="primary", size="lg")
                                    C.up_demo = gr.Button("Попробовать на демо-выписке", size="sm")
                            C.up_msg = gr.HTML()
                        C.reg_jobs = gr.HTML()
                        C.reg_table = gr.Dataframe(value=pd.DataFrame(columns=V.REGISTRY_COLUMNS), interactive=False, wrap=True, max_height=380,
                                                   label="Реестр анализов", show_search="none",
                                                   column_widths=[70, 210, 110, 220, 190, 90, 130, 130, 140, 150])
                        C.reg_sel_info = gr.HTML('<p class="muted small">Выберите строку в таблице, чтобы открыть анализ, скачать Excel или удалить его.</p>')
                        with gr.Row():
                            C.reg_open = gr.Button("Открыть анализ", variant="primary", min_width=160)
                            C.reg_excel = gr.Button("Скачать Excel", min_width=140)
                            C.reg_stop = gr.Button("Остановить", min_width=120)
                            C.reg_delete = gr.Button("Удалить", variant="stop", min_width=120)
                        C.reg_file = gr.DownloadButton("⬇ Скачать готовый файл Excel", visible=False, variant="primary", min_width=260, scale=0)
                        with gr.Row(visible=False) as C.reg_confirm:
                            gr.HTML('<div class="note warn">Анализ и загруженный файл будут удалены без возможности восстановления. '
                                    'Записи памяти риска контрагентов (история прошлых дел) сохранятся.</div>')
                            C.reg_yes = gr.Button("Да, удалить", variant="stop")
                            C.reg_no = gr.Button("Отмена")
                        C.reg_recent = gr.HTML()

                    # ═══════ Обзор ═══════
                    with gr.Tab("overview", id="overview"):
                        C.ov_head = gr.HTML()
                        C.ov_kpis = gr.HTML()
                        with gr.Row(elem_classes=["eq-row"]):
                            with gr.Column(scale=2, min_width=280):
                                C.ov_cash = gr.HTML()
                            with gr.Column(scale=4, min_width=360):
                                C.ov_month = gr.HTML()
                            with gr.Column(scale=3, min_width=340):
                                C.ov_cat = gr.HTML()
                        C.ov_top_mode = gr.Radio(TOP_MODES, value="count", label="Топ-5 контрагентов — упорядочить", container=False, elem_classes=["seg"])
                        with gr.Row(elem_classes=["eq-row"]):
                            with gr.Column(scale=2, min_width=300):
                                C.ov_dist = gr.HTML()
                            with gr.Column(scale=3, min_width=340):
                                C.ov_top = gr.HTML()

                    # ═══════ Операции ═══════
                    with gr.Tab("operations", id="operations"):
                        gr.HTML(R.page_header("Операции", "Риск 1 — операция требует проверки, риск 0 — риска нет. Сначала идут операции с риском 1. "
                                                          "Нажмите на строку, чтобы открыть карточку."))
                        with gr.Row(elem_classes=["filters"]):
                            C.op_q = gr.Textbox(label="Поиск (нажмите Enter)", placeholder="контрагент, ИНН, назначение…", scale=3, min_width=220)
                            C.op_risk = gr.Radio(V.RISK_CHOICES, value="Все", label="Риск", elem_classes=["seg"], scale=2, min_width=200)
                            C.op_dir = gr.Radio(V.DIRECTION_FILTERS, value="Все", label="Направление", elem_classes=["seg"], scale=3, min_width=260)
                            C.op_export = gr.Button("Выгрузить в Excel", variant="secondary", min_width=190, scale=0)
                            C.op_file = gr.DownloadButton("⬇ Скачать готовый файл Excel", visible=False, variant="primary", min_width=260, scale=0)
                        C.op_html = gr.HTML()
                        with gr.Row(elem_classes=["pager"]):
                            C.op_info = gr.HTML()
                            C.op_prev = gr.Button("← Назад", min_width=100, scale=0)
                            C.op_next = gr.Button("Вперёд →", min_width=100, scale=0)
                        C.op_pick = gr.Textbox(visible="hidden", elem_id="op-pick")      # сюда скрипт кладёт номер выбранной строки
                        C.op_pick_btn = gr.Button("open", visible="hidden", elem_id="op-pick-btn")

                    # ═══════ Карточка операции ═══════
                    with gr.Tab("detail", id="detail"):
                        C.dt_back = gr.Button("← К списку операций", min_width=180, scale=0)
                        C.dt_head = gr.HTML()
                        with gr.Row():
                            with gr.Column(scale=4, min_width=300):
                                C.dt_info = gr.HTML()
                                C.dt_docs = gr.HTML()
                            with gr.Column(scale=5, min_width=340):
                                C.dt_ground = gr.HTML()
                            with gr.Column(scale=4, min_width=300):
                                with gr.Group(elem_classes=["ctrl-card"]):
                                    gr.HTML('<h3>Обстоятельства, подлежащие проверке</h3>')
                                    C.dt_checks = gr.CheckboxGroup(choices=[], label="Отметьте, что уже проверено", container=False)
                                    C.dt_checks_empty = gr.HTML(visible=False)
                                    C.dt_rec = gr.Textbox(label="✎ Рекомендация — поле для правки сотрудником", lines=5, max_lines=14,
                                                          info="Текст агента можно исправить: он попадёт в отчёт и Excel.")
                                    C.dt_rec_note = gr.HTML()
                                    C.dt_decision = gr.Radio(DECISION_CHOICES, value="", label="Решение сотрудника")
                                    C.dt_comment = gr.Textbox(label="Комментарий", lines=3, placeholder="Что установлено, какие документы запрошены…")
                                    with gr.Row(elem_classes=["btn-row"]):
                                        C.dt_save = gr.Button("Сохранить", variant="primary")
                                        C.dt_rec_reset = gr.Button("Вернуть исходный текст рекомендации")
                                    C.dt_msg = gr.HTML()
                                C.dt_related = gr.HTML()

                    # ═══════ Рекомендации ═══════
                    with gr.Tab("recs", id="recs"):
                        gr.HTML(R.page_header("Рекомендации", "Что агент советует по операциям, требующим проверки — правится прямо в таблице"))
                        gr.HTML('<div class="note">Текст в колонке «Рекомендация» можно менять: дважды щёлкните по ячейке, внесите правку и нажмите '
                                '«Сохранить правки». Исходный текст агента не теряется — его можно вернуть, а в Excel выгружаются обе версии.</div>')
                        C.rc_info = gr.HTML()
                        with gr.Group(elem_classes=["ctrl-card"]):
                            with gr.Row(elem_classes=["filters"]):
                                C.rc_scope = gr.Dropdown(V.REC_SCOPES, value=V.REC_SCOPES[0], label="Какие операции показать", scale=2, min_width=240)
                                C.rc_q = gr.Textbox(label="Поиск", placeholder="контрагент, ИНН, назначение…", scale=3, min_width=240)
                                C.rc_size = gr.Dropdown(V.REC_SIZES, value="15", label="На странице", scale=1, min_width=110)
                        C.rc_table = gr.Dataframe(value=pd.DataFrame(columns=V.REC_COLUMNS), interactive=True, wrap=True, max_height=640,
                                                  static_columns=[0, 1, 2, 3, 4, 5], label="Рекомендации к правке", type="pandas",
                                                  column_widths=[46, 165, 92, 190, 104, 118, 520])
                        with gr.Row(elem_classes=["pager"]):
                            C.rc_save = gr.Button("Сохранить правки", variant="primary", scale=0, min_width=190)
                            C.rc_prev = gr.Button("← Назад", min_width=100, scale=0)
                            C.rc_page_no = gr.Number(value=1, precision=0, label="Страница", show_label=False, container=False, minimum=1, scale=0, min_width=80)
                            C.rc_next = gr.Button("Вперёд →", min_width=100, scale=0)
                        C.rc_msg = gr.HTML()

                    # ═══════ Контрагенты ═══════
                    with gr.Tab("counterparties", id="counterparties"):
                        gr.HTML(R.page_header("Контрагенты", "Анализ контрагентов и связанных операций"))
                        C.cp_kpis = gr.HTML()
                        C.cp_top_mode = gr.Radio(TOP_MODES, value="count", label="Топ-5 контрагентов — упорядочить", container=False, elem_classes=["seg"])
                        with gr.Row(elem_classes=["eq-row"]):
                            with gr.Column(scale=3, min_width=340):
                                C.cp_top = gr.HTML()
                            with gr.Column(scale=3, min_width=340):
                                C.cp_cat = gr.HTML()
                        with gr.Group(elem_classes=["ctrl-card"]):
                            with gr.Row(elem_classes=["filters"]):
                                C.cp_q = gr.Textbox(label="Поиск по контрагенту или ИНН", scale=3, min_width=260)
                                C.cp_level = gr.Dropdown(V.CP_LEVEL_CHOICES, value="Все", label="Уровень риска", scale=1, min_width=180)
                        C.cp_table = gr.Dataframe(value=pd.DataFrame(columns=V.CP_COLUMNS), interactive=False, wrap=True, max_height=460,
                                                  label="Список контрагентов",
                                                  column_widths=[112, 230, 84, 120, 100, 240, 130, 120, 110, 130])
                        C.cp_detail = gr.HTML()
                        C.cp_show_ops = gr.Button("Показать операции контрагента", visible=False, scale=0, min_width=240)

                    # ═══════ Основания оспаривания ═══════
                    with gr.Tab("grounds", id="grounds"):
                        gr.HTML(R.page_header("Основания оспаривания", "Правовые маршруты проверки, по которым агент отметил операции, и вопросы к каждому из них"))
                        C.gr_cards = gr.HTML()

                    # ═══════ Отчёты ═══════
                    with gr.Tab("reports", id="reports"):
                        gr.HTML(R.page_header("Отчёты", "Выгрузка результатов в Excel и всё, что сотрудник уже решил или поправил"))
                        C.rp_kpis = gr.HTML()
                        with gr.Row():
                            C.rp_full = gr.Button("Вся выписка (Excel)", variant="primary")
                            C.rp_marked = gr.Button("Только отмеченные для проверки")
                            C.rp_worked = gr.Button("Только с решением или правкой сотрудника")
                        C.rp_file = gr.DownloadButton("⬇ Скачать готовый файл Excel", visible=False, variant="primary", min_width=260, scale=0)
                        C.rp_msg = gr.HTML()
                        C.rp_table = gr.Dataframe(value=pd.DataFrame(columns=V.REPORT_COLUMNS), interactive=False, wrap=True, max_height=420,
                                                  label="Работа сотрудника по операциям (нажмите строку, чтобы открыть карточку)",
                                                  column_widths=[46, 165, 92, 190, 260, 104, 118, 300, 130, 180])
                        C.rp_quality = gr.HTML()

                    # ═══════ Настройки ═══════
                    with gr.Tab("settings", id="settings"):
                        gr.HTML(R.page_header("Настройки", f"Подключение к {MODEL_NAME} и параметры анализа"))
                        C.st_status = gr.HTML()
                        with gr.Group(elem_classes=["ctrl-card"]):
                            gr.HTML(f'<h3>Подключение к GigaChat</h3><p class="muted small" style="margin:4px 0 0">Модель везде одна — {MODEL_NAME}: '
                                    'распознавание выписок, классификатор, анализ операций и правовой поиск.</p>')
                            C.st_mode = gr.Radio([("Автоматически: GigaChat, если настроен", "auto"),
                                                  ("Без GigaChat: предварительная оценка по правилам", "rules")], value="auto", label="Режим анализа")
                            C.st_base_url = gr.Textbox(label="Адрес сервера GigaChat (base_url)", placeholder="https://…/v1",
                                                       info="Корпоративный контур: адрес + сертификаты ниже. Для доступа по ключу оставьте пустым.")
                            with gr.Row():
                                C.st_cert = gr.Textbox(label="Файл сертификата", placeholder="certs/cert.pem")
                                C.st_key = gr.Textbox(label="Файл закрытого ключа", placeholder="certs/private.key")
                            C.st_verify = gr.Checkbox(label="Проверять SSL-сертификат сервера")
                            with gr.Row():
                                C.st_cred = gr.Textbox(label="Ключ авторизации (credentials)", type="password",
                                                       placeholder="оставьте пустым, чтобы не менять сохранённый")
                                C.st_scope = gr.Dropdown(["GIGACHAT_API_CORP", "GIGACHAT_API_B2B", "GIGACHAT_API_PERS"], value="GIGACHAT_API_CORP",
                                                         label="Scope", allow_custom_value=True)
                        with gr.Group(elem_classes=["ctrl-card"]):
                            gr.HTML('<h3>Параметры анализа</h3>')
                            with gr.Row():
                                C.st_workers = gr.Number(label="Параллельных запросов к GigaChat", value=2, precision=0, minimum=1, maximum=32,
                                                         info="По умолчанию 2: надёжнее при ограничениях сервера. Увеличивайте, если ошибок 429/503 нет.")
                                C.st_batch = gr.Number(label="Операций в одном запросе", value=10, precision=0, minimum=1, maximum=30)
                                C.st_skip = gr.Number(label="Не анализировать операции старше (лет до заявления)", value=3, precision=0, minimum=0, maximum=10,
                                                      info="Вне периодов подозрительности: риск 0 по правилу. 0 — анализировать все.")
                            C.st_date = gr.Textbox(label="Дата заявления по умолчанию (ДД.ММ.ГГГГ)", placeholder="подставляется в форму загрузки")
                        with gr.Row():
                            C.st_save = gr.Button("Сохранить настройки", variant="primary")
                            C.st_test = gr.Button("Проверить соединение с GigaChat")
                        C.st_msg = gr.HTML()

                C.timer = gr.Timer(2.0, active=False)  # включается, когда есть задачи в работе

        # ─────────────────────────── вспомогательные функции ───────────────────────────
        nav_buttons = [C.nav[k] for k, _, _ in NAV]
        filter_inputs = [C.op_q, C.op_risk, C.op_dir]

        def nav_state(active: str) -> dict:
            highlighted = NAV_HIGHLIGHT.get(active, active)
            return {C.nav[k]: gr.update(elem_classes=["nav", "active"] if k == highlighted else ["nav"]) for k, _, _ in NAV}

        def side_status_html() -> str:
            s = SETTINGS.get()
            legal = legal_sources_status()
            on = s.llm_enabled
            pill = f'<span class="pill {"on" if on else "off"}">{"GigaChat подключён" if on else "Режим без GigaChat"}</span>'
            why = ""
            if not on:
                reason = "выбран режим «без GigaChat» в настройках" if s.mode == "rules" else s.gigachat().problem()
                why = f'<div class="why">Причина: {R.esc(reason)}</div>'
            return (f'<div class="side-status"><b>{MODEL_NAME}</b>{pill}{why}<div style="margin-top:6px">Нормативная база: {"да" if legal["normative"] else "нет"}'
                    f'<br>Судебная практика: {"да" if legal["court_practice"] else "нет"}</div></div>')

        # ───── реестр ─────
        def registry_updates() -> dict:
            metas = STORE.list()
            table = V.registry_table(metas)
            return {C.reg_kpis: V.registry_kpis(metas), C.reg_jobs: R.active_jobs_html(metas),
                    C.reg_table: V.registry_styled(table) if len(table) else table, C.reg_recent: R.recent_uploads_html(metas)}

        def signature() -> tuple[str, str]:
            metas = STORE.list()
            return (",".join(f"{m['id']}:{m['status']}:{m.get('n_ops')}" for m in metas),
                    ",".join(f"{m['id']}:{round(float(m.get('progress') or 0), 2)}:{m.get('message')}" for m in metas
                             if m["status"] in ACTIVE_STATUSES))

        def poll(current_aid, sig):  # noqa: ANN001
            new_sig = signature()
            metas = STORE.list()
            running = gr.Timer(active=any(m["status"] in ACTIVE_STATUSES for m in metas))  # без задач таймер выключается
            if new_sig == tuple(sig):
                return {poll_sig: gr.skip(), C.timer: running}
            out = {poll_sig: new_sig, C.timer: running}
            out[C.reg_jobs] = R.active_jobs_html(metas)
            out[C.reg_recent] = R.recent_uploads_html(metas)
            if new_sig[0] != tuple(sig)[0]:
                table = V.registry_table(metas)
                out[C.reg_kpis] = V.registry_kpis(metas)
                out[C.reg_table] = V.registry_styled(table) if len(table) else table
                choices = analysis_choices()
                out[C.analysis_dd] = gr.update(choices=choices, value=current_aid if any(v == current_aid for _, v in choices) else None)
            return out

        # ───── страницы ─────
        def render_empty(page: str) -> dict:
            blank = empty()
            if page == "overview":
                return {C.ov_head: blank, C.ov_kpis: "", C.ov_cash: "", C.ov_month: "", C.ov_cat: "", C.ov_dist: "", C.ov_top: ""}
            if page == "operations":
                return {C.op_html: blank, C.op_info: ""}
            if page == "grounds":
                return {C.gr_cards: blank}
            if page == "reports":
                return {C.rp_kpis: blank, C.rp_table: pd.DataFrame(columns=V.REPORT_COLUMNS), C.rp_quality: ""}
            if page == "recs":
                return {C.rc_info: blank, C.rc_table: pd.DataFrame(columns=V.REC_COLUMNS)}
            if page == "counterparties":
                return {C.cp_kpis: blank, C.cp_top: "", C.cp_cat: "", C.cp_table: pd.DataFrame(columns=V.CP_COLUMNS), C.cp_detail: ""}
            return {}

        def render_overview(ctx: V.Context, top_mode: str) -> dict:
            cards = V.overview_cards(ctx, top_mode)
            return {C.ov_head: V.overview_head(ctx), C.ov_kpis: V.overview_kpis(ctx), C.ov_cash: cards["cash"], C.ov_month: cards["month"],
                    C.ov_cat: cards["cat"], C.ov_dist: cards["dist"], C.ov_top: cards["top"]}

        def render_operations(ctx: V.Context, page: int, *flt: Any) -> dict:
            rows, info, pages_total, _ = V.operations_rows(ctx, V.make_filters(*flt), page)
            return {C.op_html: R.ops_table_html(rows), C.op_info: f'<div class="pager-info">{R.esc(info)}</div>',
                    op_page: min(max(page, 0), pages_total - 1)}

        def render_recs(ctx: V.Context, scope: str, query: str, page: int, size: int) -> dict:
            table, info, pages_total = V.recommendations_page(ctx, scope, query, page, size)
            real_page = min(max(page, 0), pages_total - 1)
            return {C.rc_info: f'<div class="pager-info">{R.esc(info)}</div>', C.rc_table: table, C.rc_page_no: real_page + 1}

        def render_counterparties(ctx: V.Context, top_mode: str, query: str, level: str) -> dict:
            cards = V.counterparties_cards(ctx, top_mode)
            table = V.counterparties_table(ctx, query, level)
            return {C.cp_kpis: V.counterparties_kpis(ctx), C.cp_top: cards["top"], C.cp_cat: cards["cat"],
                    C.cp_table: V.cp_styled(table) if len(table) else table}

        def render_reports(ctx: V.Context) -> dict:
            table = V.report_table(ctx)
            return {C.rp_kpis: V.report_kpis(ctx), C.rp_table: V.styled(table) if len(table) else table, C.rp_quality: V.quality_block(ctx)}

        def settings_values() -> dict:
            s = SETTINGS.get()
            return {C.st_mode: s.mode, C.st_base_url: s.base_url, C.st_cert: s.cert_file, C.st_key: s.key_file, C.st_verify: s.verify_ssl,
                    C.st_cred: "", C.st_scope: s.scope, C.st_workers: s.llm_max_workers, C.st_batch: s.max_operations_per_llm_batch,
                    C.st_skip: s.rule_skip_older_than_years, C.st_date: s.default_filing_date, C.st_status: settings_status_html(s),
                    C.up_date: gr.update(value=s.default_filing_date) if s.default_filing_date else gr.skip(),
                    C.side_status: side_status_html()}

        def settings_status_html(s: AppSettings) -> str:
            legal = legal_sources_status()
            rows = [
                ("Режим анализа", f'<span class="pill {"on" if s.llm_enabled else "off"}">{"GigaChat-3-Ultra" if s.llm_enabled else "по правилам, без GigaChat"}</span>'
                 + ("" if s.llm_enabled else f' <span class="muted small">Причина: {R.esc("выбран режим «без GigaChat»" if s.mode == "rules" else s.gigachat().problem())}</span>')),
                ("Нормативная база (FAISS)", "подключена" if legal["normative"] else "не найдена — правовой поиск по нормам не выполняется"),
                ("Судебная практика (FAISS + SQLite)", "подключена" if legal["court_practice"] else "не найдена — поиск практики не выполняется"),
                ("Память риска контрагентов", "доступна" if get_risk_db() is not None else "недоступна"),
                ("Папка данных", str(DATA_DIR)),
            ]
            body = "".join(f'<div class="kv"><span>{R.esc(k)}</span><b>{v if k == "Режим анализа" else R.esc(v)}</b></div>' for k, v in rows)
            return R.card("Состояние", body, "check")

        def refresh(page: str, current_aid, *rest):  # noqa: ANN001, ANN002
            """Полная перерисовка страницы page. rest = фильтры операций (3), страница, режимы топа, фильтры контрагентов."""
            (q, risk, d, page_no, ov_mode, cp_mode, cq, clevel) = rest
            out: dict = nav_state(page)
            out[pages] = gr.Tabs(selected=page)
            ctx = context_for(current_aid)
            if page == "registry":
                out.update(registry_updates())
            elif page == "settings":
                out.update(settings_values())
            elif ctx is None:
                out.update(render_empty(page))
            elif page == "overview":
                out.update(render_overview(ctx, ov_mode))
            elif page == "operations":
                out.update(render_operations(ctx, int(page_no or 0), q, risk, d))
            elif page == "grounds":
                out[C.gr_cards] = V.grounds_block(ctx)
            elif page == "reports":
                out.update(render_reports(ctx))
            elif page == "recs":
                out.update(render_recs(ctx, V.REC_SCOPES[0], "", 0, 15))
                out.update({C.rc_scope: V.REC_SCOPES[0], C.rc_q: "", C.rc_size: "15"})
            elif page == "counterparties":
                out.update(render_counterparties(ctx, cp_mode, cq, clevel))
            out[C.period] = V.period_html(ctx.meta if ctx else None)
            return out

        page_inputs = [aid, *filter_inputs, op_page, C.ov_top_mode, C.cp_top_mode, C.cp_q, C.cp_level]
        all_page_outputs = [
            pages, C.period, C.side_status, *nav_buttons,
            C.reg_kpis, C.reg_jobs, C.reg_table, C.reg_recent, C.up_date,
            C.ov_head, C.ov_kpis, C.ov_cash, C.ov_month, C.ov_cat, C.ov_dist, C.ov_top,
            C.op_html, C.op_info, op_page,
            C.rc_info, C.rc_table, C.rc_page_no, C.rc_scope, C.rc_q, C.rc_size,
            C.cp_kpis, C.cp_top, C.cp_cat, C.cp_table, C.cp_detail, C.gr_cards, C.rp_kpis, C.rp_table, C.rp_quality,
            C.st_mode, C.st_base_url, C.st_cert, C.st_key, C.st_verify, C.st_cred, C.st_scope, C.st_workers, C.st_batch, C.st_skip,
            C.st_date, C.st_status,
        ]

        for key, _, _ in NAV:
            C.nav[key].click(lambda current_aid, *rest, _k=key: refresh(_k, current_aid, *rest), page_inputs, all_page_outputs,
                             show_progress="hidden")

        # ───── выбор анализа ─────
        def on_select_analysis(new_aid, *rest):  # noqa: ANN001, ANN002
            """Смена анализа: сброс фильтров операций и перерисовка текущей страницы (остаёмся на обзоре, если были в реестре)."""
            out = {aid: new_aid, sel_idx: None}
            target = "overview"
            reset = ["", "Все", "Все", 0, "count", "count", "", "Все"]
            out.update(refresh(target, new_aid, *reset))
            out[op_page] = 0
            for comp, value in zip(filter_inputs, reset[:3]):
                out.setdefault(comp, value)
            return out

        C.analysis_dd.input(on_select_analysis, [C.analysis_dd, *page_inputs[1:]], [aid, sel_idx, *all_page_outputs, *filter_inputs],
                            show_progress="hidden")

        # ───── реестр: выбор строки ─────
        def reg_select(evt: gr.SelectData):
            row = evt.row_value if getattr(evt, "row_value", None) else None
            code = str(row[0]) if row else None
            meta = V.meta_by_code(STORE.list(), code) if code else None
            if meta is None:
                return None, '<p class="muted small">Строка не найдена — обновите страницу.</p>'
            info = (f'<p class="small">Выбран анализ: <b>{R.esc(meta.get("org_name") or meta["filename"])}</b> · {R.esc(meta["filename"])} · '
                    f'{R.esc(R.JOB_STATUS.get(meta["status"], ("",))[0])}'
                    + (f' · <span class="neg">{R.esc(meta["error"])}</span>' if meta.get("error") else "") + "</p>")
            return code, info

        C.reg_table.select(reg_select, None, [reg_code, C.reg_sel_info], show_progress="hidden")

        def reg_open(code, *rest):  # noqa: ANN001, ANN002
            meta = V.meta_by_code(STORE.list(), code) if code else None
            if meta is None:
                return {C.reg_sel_info: '<div class="note warn">Сначала выберите строку в таблице.</div>'}
            if meta["status"] != "done":
                return {C.reg_sel_info: '<div class="note warn">Этот анализ ещё не завершён или завершился ошибкой — открыть нечего.</div>'}
            return on_select_analysis(meta["id"], *rest) | {C.analysis_dd: gr.update(choices=analysis_choices(), value=meta["id"])}

        C.reg_open.click(reg_open, [reg_code, *page_inputs[1:]], [aid, sel_idx, C.analysis_dd, C.reg_sel_info, *all_page_outputs, *filter_inputs])

        def reg_excel(code):  # noqa: ANN001
            meta = V.meta_by_code(STORE.list(), code) if code else None
            if meta is None or meta["status"] != "done":
                return gr.update(visible=False), '<div class="note warn">Выберите завершённый анализ в таблице.</div>'
            ctx = context_for(meta["id"])
            path = export_to_temp(ctx.df, f"statement_{meta['id'][-6:]}", reviews=ctx.reviews, risk_db=ctx.risk_db)
            return gr.update(value=path, visible=True), ""

        C.reg_excel.click(reg_excel, [reg_code], [C.reg_file, C.reg_sel_info])

        def reg_stop(code):  # noqa: ANN001
            meta = V.meta_by_code(STORE.list(), code) if code else None
            if meta is None or meta["status"] not in ACTIVE_STATUSES:
                return '<div class="note warn">Выберите анализ, который сейчас обрабатывается или стоит в очереди.</div>'
            JOBS.cancel(meta["id"])
            return '<div class="note">Остановка запрошена: анализ завершится после текущего шага.</div>'

        C.reg_stop.click(reg_stop, [reg_code], [C.reg_sel_info])
        C.reg_delete.click(lambda code: gr.update(visible=bool(code)), [reg_code], [C.reg_confirm])
        C.reg_no.click(lambda: gr.update(visible=False), None, [C.reg_confirm])

        def reg_delete_yes(code, current_aid):  # noqa: ANN001
            meta = V.meta_by_code(STORE.list(), code) if code else None
            out: dict = {C.reg_confirm: gr.update(visible=False), reg_code: None}
            if meta is None:
                return out
            if meta["status"] in ACTIVE_STATUSES:
                out[C.reg_sel_info] = '<div class="note warn">Анализ ещё выполняется: сначала остановите его.</div>'
                return out
            STORE.delete(meta["id"])
            out[C.reg_sel_info] = '<div class="note">Анализ удалён.</div>'
            out.update(registry_updates())
            choices = analysis_choices()
            keep = current_aid if any(v == current_aid for _, v in choices) else None
            out[C.analysis_dd] = gr.update(choices=choices, value=keep)
            out[aid] = keep
            return out

        C.reg_yes.click(reg_delete_yes, [reg_code, aid], [C.reg_confirm, reg_code, C.reg_sel_info, C.reg_kpis, C.reg_jobs, C.reg_table,
                                                         C.reg_recent, C.analysis_dd, aid])

        # ───── загрузка ─────
        def upload(files, filing_date):  # noqa: ANN001
            if not files:
                return {C.up_msg: '<div class="note warn">Выберите хотя бы один файл выписки.</div>'}
            date_text = (filing_date or "").strip() or SETTINGS.get().default_filing_date
            if not date_text:
                return {C.up_msg: '<div class="note warn">Укажите дату заявления о банкротстве — от неё считаются периоды подозрительности.</div>'}
            try:
                parse_filing_date(date_text)
            except ValueError as exc:
                return {C.up_msg: f'<div class="note warn">{R.esc(exc)}</div>'}
            if parse_filing_date(date_text) > date.today():
                return {C.up_msg: '<div class="note warn">Дата заявления в будущем — проверьте, нет ли опечатки.</div>'}
            accepted, rejected = [], []
            for f in files:
                path = Path(getattr(f, "name", f))
                if path.suffix.lower() not in SUPPORTED_SUFFIXES:
                    rejected.append(f"{path.name}: формат {path.suffix or '—'} не поддерживается")
                    continue
                try:
                    JOBS.submit(path, date_text, original_name=path.name)
                    accepted.append(path.name)
                except Exception as exc:  # noqa: BLE001
                    logger.exception("Не удалось поставить в очередь %s", path)
                    rejected.append(f"{path.name}: {exc}")
            msg = ""
            if accepted:
                msg += f'<div class="note">В очередь поставлено: {R.esc(", ".join(accepted))}. Ход обработки — в блоке «Сейчас обрабатывается» ниже.</div>'
            if rejected:
                msg += '<div class="note warn">Не принято:<br>' + "<br>".join(R.esc(r) for r in rejected) + "</div>"
            out = {C.up_msg: msg, poll_sig: ("", "")}
            if accepted:
                out[C.up_files] = None
                out[C.timer] = gr.Timer(active=True)
            out.update(registry_updates())
            return out

        def upload_demo():  # noqa: ANN202
            demo_file = Path(__file__).resolve().parents[2] / "examples" / "demo_statement.xlsx"
            if not demo_file.exists():
                return {C.up_msg: '<div class="note warn">Демо-выписка не найдена (examples/demo_statement.xlsx).</div>'}
            JOBS.submit(demo_file, "10.10.2025", original_name="demo_statement.xlsx")
            out = {C.up_msg: '<div class="note">Демо-выписка (учебные данные) поставлена в очередь, дата заявления — 10.10.2025.</div>', poll_sig: ("", ""),
                   C.timer: gr.Timer(active=True)}
            out.update(registry_updates())
            return out

        C.up_demo.click(upload_demo, None, [C.up_msg, poll_sig, C.timer, C.reg_kpis, C.reg_jobs, C.reg_table, C.reg_recent])
        C.up_btn.click(upload, [C.up_files, C.up_date], [C.up_msg, C.up_files, poll_sig, C.timer, C.reg_kpis, C.reg_jobs, C.reg_table, C.reg_recent])
        C.timer.tick(poll, [aid, poll_sig], [poll_sig, C.timer, C.reg_jobs, C.reg_recent, C.reg_kpis, C.reg_table, C.analysis_dd], show_progress="hidden")

        # ───── рекомендации ─────
        def rc_refresh(current_aid, scope, query, page_no, size):  # noqa: ANN001
            ctx = context_for(current_aid)
            if ctx is None:
                return {}
            return render_recs(ctx, scope, query, int(page_no or 1) - 1, int(size))

        rc_outputs = [C.rc_info, C.rc_table, C.rc_page_no]
        rc_inputs = [aid, C.rc_scope, C.rc_q, C.rc_page_no, C.rc_size]
        # .input / .submit / .blur — только действия пользователя: программная смена значений (при открытии страницы, смене анализа)
        # не должна запускать лавину запросов, как это делал .change
        for comp in (C.rc_scope, C.rc_size):
            comp.input(lambda current_aid, scope, query, _p, size: rc_refresh(current_aid, scope, query, 1, size), rc_inputs, rc_outputs,
                       show_progress="hidden")
        for event in (C.rc_q.submit, C.rc_q.blur):
            event(lambda current_aid, scope, query, _p, size: rc_refresh(current_aid, scope, query, 1, size), rc_inputs, rc_outputs,
                  show_progress="hidden", trigger_mode="always_last")
        C.rc_page_no.submit(rc_refresh, rc_inputs, rc_outputs, show_progress="hidden")
        C.rc_prev.click(lambda current_aid, scope, query, page_no, size: rc_refresh(current_aid, scope, query, max(int(page_no or 1) - 1, 1), size),
                        rc_inputs, rc_outputs, show_progress="hidden")
        C.rc_next.click(lambda current_aid, scope, query, page_no, size: rc_refresh(current_aid, scope, query, int(page_no or 1) + 1, size),
                        rc_inputs, rc_outputs, show_progress="hidden")

        def rc_save(current_aid, scope, query, page_no, size, table):  # noqa: ANN001
            ctx = context_for(current_aid)
            if ctx is None:
                return {}
            saved, reset = V.save_recommendation_edits(ctx, scope, query, int(page_no or 1) - 1, int(size), table, STORE)
            ctx = context_for(current_aid)  # перечитать решения
            out = render_recs(ctx, scope, query, int(page_no or 1) - 1, int(size))
            if saved or reset:
                note = f"Сохранено правок: {saved}" + (f", возвращено к исходному тексту: {reset}" if reset else "") + "."
            else:
                note = "Изменений не найдено: отредактируйте текст в колонке «Рекомендация» и нажмите «Сохранить правки»."
            out[C.rc_msg] = f'<div class="note">{R.esc(note)}</div>'
            return out

        C.rc_save.click(rc_save, [*rc_inputs, C.rc_table], [*rc_outputs, C.rc_msg])

        # ───── обзор / контрагенты: режимы топа ─────
        def ov_mode_change(current_aid, mode):  # noqa: ANN001
            ctx = context_for(current_aid)
            return V.overview_cards(ctx, mode)["top"] if ctx else ""

        C.ov_top_mode.input(ov_mode_change, [aid, C.ov_top_mode], [C.ov_top], show_progress="hidden")

        def cp_refresh(current_aid, mode, query, level):  # noqa: ANN001
            ctx = context_for(current_aid)
            if ctx is None:
                return {}
            return render_counterparties(ctx, mode, query, level)

        for comp in (C.cp_top_mode, C.cp_level):
            comp.input(cp_refresh, [aid, C.cp_top_mode, C.cp_q, C.cp_level], [C.cp_kpis, C.cp_top, C.cp_cat, C.cp_table], show_progress="hidden")
        for event in (C.cp_q.submit, C.cp_q.blur):
            event(cp_refresh, [aid, C.cp_top_mode, C.cp_q, C.cp_level], [C.cp_kpis, C.cp_top, C.cp_cat, C.cp_table],
                  show_progress="hidden", trigger_mode="always_last")

        def cp_select(current_aid, evt: gr.SelectData):  # noqa: ANN001
            ctx = context_for(current_aid)
            row = getattr(evt, "row_value", None)
            if ctx is None or not row:
                return {}
            inn = str(row[0])
            return {cp_inn: inn, C.cp_detail: V.counterparty_detail(ctx, inn), C.cp_show_ops: gr.update(visible=True)}

        C.cp_table.select(cp_select, [aid], [cp_inn, C.cp_detail, C.cp_show_ops], show_progress="hidden")

        def cp_show_ops(current_aid, inn, *rest):  # noqa: ANN001, ANN002
            ctx = context_for(current_aid)
            if ctx is None or not inn:
                return {}
            flt = [str(inn), "Все", "Все"]
            out = refresh("operations", current_aid, *flt, 0, *rest[-4:])
            out.update(dict(zip(filter_inputs, flt)))
            return out

        C.cp_show_ops.click(cp_show_ops, [aid, cp_inn, *page_inputs[1:]], [*all_page_outputs, *filter_inputs])

        # ───── операции: фильтры, страницы, выгрузка ─────
        def ops_go(current_aid, page, q, risk, direction):  # noqa: ANN001
            ctx = context_for(current_aid)
            if ctx is None:
                return {}
            return render_operations(ctx, int(page or 0), q, risk, direction)

        ops_outputs = [C.op_html, C.op_info, op_page]
        ops_inputs = [aid, op_page, *filter_inputs]
        for comp in (C.op_risk, C.op_dir):
            comp.input(lambda current_aid, _page, *flt: ops_go(current_aid, 0, *flt), ops_inputs, ops_outputs, show_progress="hidden")
        for event in (C.op_q.submit, C.op_q.blur):  # поиск — по Enter или при уходе из поля, а не на каждую букву
            event(lambda current_aid, _page, *flt: ops_go(current_aid, 0, *flt), ops_inputs, ops_outputs, show_progress="hidden",
                  trigger_mode="always_last")
        C.op_prev.click(lambda current_aid, page, *flt: ops_go(current_aid, max(int(page or 0) - 1, 0), *flt), ops_inputs, ops_outputs,
                        show_progress="hidden")
        C.op_next.click(lambda current_aid, page, *flt: ops_go(current_aid, int(page or 0) + 1, *flt), ops_inputs, ops_outputs,
                        show_progress="hidden")

        def ops_export(current_aid, *flt):  # noqa: ANN001, ANN002
            ctx = context_for(current_aid)
            if ctx is None:
                return gr.update(visible=False)
            filtered = V.filtered_operations(ctx, V.make_filters(*flt))
            path = export_to_temp(ctx.df, f"operations_{ctx.meta['id'][-6:]}", reviews=ctx.reviews, risk_db=ctx.risk_db,
                                  only_idx=set(filtered["idx"].astype(str)))
            return gr.update(value=path, visible=True)

        C.op_export.click(ops_export, [aid, *filter_inputs], [C.op_file], show_progress="minimal")

        # ───── карточка операции ─────
        detail_outputs = [aid, sel_idx, pages, C.period, *nav_buttons, C.dt_head, C.dt_info, C.dt_ground, C.dt_docs, C.dt_related, C.dt_checks,
                          C.dt_checks_empty, C.dt_rec, C.dt_rec_note, C.dt_decision, C.dt_comment, C.dt_msg]

        def rec_note(blocks: dict) -> str:
            if not blocks["rec_edited"]:
                return ""
            return ('<div class="note"><b>Рекомендация изменена сотрудником.</b> Исходный текст агента: '
                    f'{R.esc(blocks["recommendation_original"])}</div>')

        def detail_updates(ctx: V.Context, idx: str) -> dict:
            blocks = V.detail_blocks(ctx, idx)
            if blocks is None:
                return {}
            missing = blocks["missing"]
            out = nav_state("detail")
            out.update({
                pages: gr.Tabs(selected="detail"), sel_idx: idx, C.period: V.period_html(ctx.meta), C.dt_head: blocks["head"],
                C.dt_info: blocks["info"], C.dt_ground: blocks["ground"], C.dt_docs: blocks["docs"], C.dt_related: blocks["related"],
                C.dt_checks: gr.update(choices=missing, value=blocks["checked"], visible=bool(missing)),
                C.dt_checks_empty: gr.update(value='<p class="muted small">Для этой операции отдельных вопросов к проверке нет.</p>', visible=not missing),
                C.dt_rec: blocks["recommendation"], C.dt_rec_note: rec_note(blocks),
                C.dt_decision: blocks["decision"], C.dt_comment: blocks["comment"],
                C.dt_msg: ""})
            return out

        def ops_open(current_aid, pick, *flt):  # noqa: ANN001, ANN002
            """Клик по строке HTML-таблицы: скрипт кладёт в скрытое поле «номер:время»; номер — позиция в отфильтрованном списке."""
            ctx = context_for(current_aid)
            try:
                position = int(str(pick).split(":")[0])
            except (TypeError, ValueError):
                return {}
            if ctx is None:
                return {}
            filtered = V.filtered_operations(ctx, V.make_filters(*flt))
            if not 1 <= position <= len(filtered):
                return {}
            return detail_updates(ctx, str(filtered.iloc[position - 1]["idx"]))

        C.op_pick_btn.click(ops_open, [aid, C.op_pick, *filter_inputs], detail_outputs[1:], show_progress="hidden")

        def report_table_select(current_aid, evt: gr.SelectData):  # noqa: ANN001
            ctx = context_for(current_aid)
            row = getattr(evt, "row_value", None)
            if ctx is None or not row:
                return {}
            frame = V.worked_frame(ctx)
            try:
                return detail_updates(ctx, str(frame.iloc[int(row[0]) - 1]["idx"]))
            except (IndexError, ValueError):
                return {}

        C.rp_table.select(report_table_select, [aid], detail_outputs[1:], show_progress="hidden")

        def dt_back(current_aid, *rest):  # noqa: ANN001, ANN002
            return refresh("operations", current_aid, *rest)

        C.dt_back.click(dt_back, page_inputs, all_page_outputs, show_progress="hidden")

        def dt_save(current_aid, idx, checked, rec, decision, comment):  # noqa: ANN001
            if not current_aid or not idx:
                return {}
            STORE.save_review(current_aid, idx, decision or "", comment or "", list(checked or []))
            ctx = context_for(current_aid)
            blocks = V.detail_blocks(ctx, idx)
            STORE.set_recommendation(current_aid, idx, rec or "", blocks["recommendation_original"])
            ctx = context_for(current_aid)
            blocks = V.detail_blocks(ctx, idx)
            return {C.dt_msg: '<div class="note">Сохранено: решение, комментарий и рекомендация.</div>', C.dt_info: blocks["info"],
                    C.dt_rec_note: rec_note(blocks), C.dt_rec: blocks["recommendation"]}

        C.dt_save.click(dt_save, [aid, sel_idx, C.dt_checks, C.dt_rec, C.dt_decision, C.dt_comment],
                        [C.dt_msg, C.dt_info, C.dt_rec_note, C.dt_rec], show_progress="hidden")

        def dt_rec_reset(current_aid, idx):  # noqa: ANN001
            if not current_aid or not idx:
                return {}
            ctx = context_for(current_aid)
            blocks = V.detail_blocks(ctx, idx)
            STORE.set_recommendation(current_aid, idx, "", blocks["recommendation_original"])
            return {C.dt_rec: blocks["recommendation_original"], C.dt_rec_note: "",
                    C.dt_msg: '<div class="note">Возвращена рекомендация агента.</div>'}

        C.dt_rec_reset.click(dt_rec_reset, [aid, sel_idx], [C.dt_rec, C.dt_rec_note, C.dt_msg], show_progress="hidden")

        # ───── отчёты: выгрузка ─────
        def make_report(kind: str):  # noqa: ANN202
            def handler(current_aid):  # noqa: ANN001
                ctx = context_for(current_aid)
                if ctx is None:
                    return gr.update(visible=False), '<div class="note warn">Сначала выберите анализ.</div>'
                only = None
                if kind == "marked":
                    only = set(ctx.df.loc[ctx.df["status"] == "disputable", "idx"].astype(str))
                elif kind == "worked":
                    only = set(V.worked_frame(ctx)["idx"].astype(str))
                    if not only:
                        return gr.update(visible=False), '<div class="note warn">Сотрудник пока не принимал решений и не правил рекомендации.</div>'
                title = {"full": "Выписка", "marked": "Отмеченные", "worked": "Работа сотрудника"}[kind]
                path = export_to_temp(ctx.df, f"{kind}_{ctx.meta['id'][-6:]}", reviews=ctx.reviews, risk_db=ctx.risk_db, only_idx=only, title=title)
                return gr.update(value=path, visible=True), ""
            return handler

        for button, kind in ((C.rp_full, "full"), (C.rp_marked, "marked"), (C.rp_worked, "worked")):
            button.click(make_report(kind), [aid], [C.rp_file, C.rp_msg])

        # ───── настройки ─────
        def read_settings(mode, base_url, cert, key, verify, cred, scope, workers, batch, skip, default_date) -> dict:  # noqa: ANN001
            current = SETTINGS.get()
            values = dict(mode=mode, base_url=(base_url or "").strip(), cert_file=(cert or "").strip() or "certs/cert.pem",
                          key_file=(key or "").strip() or "certs/private.key", verify_ssl=bool(verify),
                          credentials=(cred or "").strip() or current.credentials, scope=scope or "GIGACHAT_API_CORP",
                          llm_max_workers=int(workers or 2), max_operations_per_llm_batch=int(batch or 10),
                          rule_skip_older_than_years=int(skip or 0), default_filing_date=(default_date or "").strip())
            return values

        settings_inputs = [C.st_mode, C.st_base_url, C.st_cert, C.st_key, C.st_verify, C.st_cred, C.st_scope, C.st_workers, C.st_batch,
                           C.st_skip, C.st_date]

        def st_save(*values):  # noqa: ANN002
            data = read_settings(*values)
            if data["default_filing_date"]:
                try:
                    parse_filing_date(data["default_filing_date"])
                except ValueError as exc:
                    return {C.st_msg: f'<div class="note warn">{R.esc(exc)}</div>'}
            s = SETTINGS.update(**data)
            return {C.st_msg: '<div class="note">Настройки сохранены. Они применяются к новым анализам.</div>', C.st_cred: "",
                    C.st_status: settings_status_html(s), C.side_status: side_status_html(),
                    C.up_date: gr.update(value=s.default_filing_date) if s.default_filing_date else gr.skip()}

        C.st_save.click(st_save, settings_inputs, [C.st_msg, C.st_cred, C.st_status, C.side_status, C.up_date], show_progress="hidden")

        def st_test(*values):  # noqa: ANN002
            data = read_settings(*values)
            candidate = AppSettings(**{**SETTINGS.get().__dict__, **data})
            ok, message = check_gigachat(candidate)
            return f'<div class="note {"" if ok else "warn"}">{R.esc(message)}</div>'

        C.st_test.click(st_test, settings_inputs, [C.st_msg], show_progress="full")

        # ───── начальная загрузка ─────
        def on_load():  # noqa: ANN202
            choices = analysis_choices()
            first = choices[0][1] if choices else None
            out = {aid: first, C.analysis_dd: gr.update(choices=choices, value=first), poll_sig: signature(),
                   C.timer: gr.Timer(active=any(m["status"] in ACTIVE_STATUSES for m in STORE.list()))}
            out.update(refresh("registry", first, "", "Все", "Все", 0, "count", "count", "", "Все"))
            out.update(settings_values())
            return out

        demo.load(on_load, None, [aid, C.analysis_dd, poll_sig, C.timer, *all_page_outputs], show_progress="hidden")
    return demo
