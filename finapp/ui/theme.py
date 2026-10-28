"""Тёмная тема «Банкротный анализ»: переменные Gradio + CSS компонентов приложения."""
from __future__ import annotations

import gradio as gr

THEME = gr.themes.Base(
    primary_hue=gr.themes.colors.emerald, secondary_hue=gr.themes.colors.cyan, neutral_hue=gr.themes.colors.slate,
    font=[gr.themes.GoogleFont("Inter"), "ui-sans-serif", "system-ui", "-apple-system", "Segoe UI", "Roboto", "Arial", "sans-serif"],
    radius_size=gr.themes.sizes.radius_md,
)

# Принудительно тёмная схема: Gradio выбирает тему по настройкам системы, поэтому переменные заданы и для :root, и для .dark.
CSS = """
:root, .dark, body {
  --bg: #08131b; --bg2: #0b1a24; --panel: #0e202b; --panel2: #12293a; --line: #1c3647; --line2: #26485d;
  --text: #e8f2f6; --muted: #8ba3b2; --accent: #25e3a5; --accent-d: #12b886; --blue: #3ac3ff; --red: #ff6b6b; --amber: #f5b83d; --gray: #8b98a9;
  color-scheme: dark;
  --body-background-fill: var(--bg); --background-fill-primary: var(--panel); --background-fill-secondary: var(--bg2);
  --block-background-fill: transparent; --block-border-color: transparent; --block-border-width: 0px; --block-shadow: none;
  --body-text-color: var(--text); --body-text-color-subdued: var(--muted); --block-label-text-color: var(--muted);
  --block-title-text-color: var(--muted); --border-color-primary: var(--line); --border-color-accent: var(--accent);
  --input-background-fill: #0a1822; --input-background-fill-focus: #0c1d29; --input-border-color: var(--line2);
  --input-border-color-focus: var(--accent); --input-placeholder-color: #5f7888; --input-shadow: none; --input-shadow-focus: 0 0 0 2px rgba(37,227,165,.25);
  --button-primary-background-fill: var(--accent); --button-primary-background-fill-hover: #4af0b9; --button-primary-text-color: #04251a;
  --button-primary-border-color: var(--accent); --button-secondary-background-fill: #10283a; --button-secondary-background-fill-hover: #17364b;
  --button-secondary-text-color: var(--text); --button-secondary-border-color: var(--line2);
  --table-even-background-fill: #0d1e29; --table-odd-background-fill: #0a1822; --table-row-focus: #15364a; --table-border-color: var(--line);
  --table-text-color: var(--text); --checkbox-background-color: #0a1822; --checkbox-border-color: var(--line2);
  --checkbox-background-color-selected: var(--accent); --checkbox-border-color-selected: var(--accent); --checkbox-label-background-fill: transparent;
  --checkbox-label-background-fill-hover: transparent; --checkbox-label-text-color: var(--text);
  --panel-background-fill: var(--panel); --panel-border-color: var(--line); --color-accent: var(--accent); --color-accent-soft: rgba(37,227,165,.14);
  --link-text-color: var(--accent); --link-text-color-hover: #4af0b9; --loader-color: var(--accent); --slider-color: var(--accent);
}
html, body, gradio-app, .gradio-container { background: var(--bg) !important; }
body::before { content: ""; position: fixed; inset: 0; z-index: -1; pointer-events: none; background: radial-gradient(1200px 600px at 85% -10%, #0f2a38 0%, transparent 60%); }
.gradio-container { max-width: 100% !important; padding: 0 !important; font-feature-settings: "tnum"; }
footer, .gradio-container > footer { display: none !important; }
.muted { color: var(--muted); } .small { font-size: 12.5px; } .num { text-align: right; font-variant-numeric: tabular-nums; }
.pos { color: var(--accent); } .neg { color: var(--red); }

/* каркас */
#shell { gap: 0 !important; align-items: stretch; flex-wrap: nowrap !important; height: 100vh; overflow: hidden; }
#sidebar { background: linear-gradient(180deg, #0a1b26, #08141c); border-right: 1px solid var(--line); min-width: 236px !important; max-width: 236px !important;
  padding: 18px 12px !important; height: 100vh; gap: 4px !important; overflow-y: auto; flex-wrap: nowrap !important; }
#main { padding: 18px 28px 40px !important; min-width: 0; gap: 14px !important; height: 100vh; overflow-y: auto; flex-wrap: nowrap !important; }
.brand { display: flex; align-items: center; gap: 10px; padding: 4px 8px 18px; }
.brand-logo { width: 36px; height: 36px; border-radius: 10px; background: linear-gradient(145deg, #12b886, #25e3a5); display: grid; place-items: center; color: #04251a; }
.brand-name { font-weight: 800; line-height: 1.05; font-size: 16px; letter-spacing: .1px; } .brand-sub { color: var(--muted); font-size: 11.5px; margin-top: 2px; }
#sidebar button.nav { justify-content: flex-start !important; text-align: left; background: transparent !important; border: 1px solid transparent !important; color: #b8cad5 !important;
  font-weight: 600; padding: 11px 14px !important; border-radius: 10px !important; box-shadow: none !important; min-height: 44px; }
#sidebar button.nav:hover { background: rgba(37,227,165,.07) !important; color: var(--text) !important; }
#sidebar button.nav.active { background: linear-gradient(90deg, rgba(37,227,165,.22), rgba(37,227,165,.06)) !important; border-color: rgba(37,227,165,.45) !important; color: #eafff7 !important; }
#side-foot { margin-top: auto; }
.side-status { font-size: 12px; color: var(--muted); border: 1px solid var(--line); border-radius: 10px; padding: 10px 12px; background: rgba(255,255,255,.02); }
.side-status b { color: var(--text); display: block; margin-bottom: 2px; }
.side-status .why { margin-top: 6px; color: #f6dba4; font-size: 11.5px; line-height: 1.35; }
.pill { display: inline-flex; align-items: center; gap: 6px; padding: 2px 9px; border-radius: 99px; font-size: 11.5px; font-weight: 700; }
.pill.on { background: rgba(37,227,165,.16); color: var(--accent); } .pill.off { background: rgba(245,184,61,.15); color: var(--amber); }

/* верхняя панель */
#topbar { align-items: flex-end !important; gap: 12px !important; flex-wrap: wrap !important; }
#topbar .block { min-width: 0; }
.period-box { color: var(--muted); font-size: 13px; padding-bottom: 8px; line-height: 1.4; } .period-box b { color: var(--text); }
button.primary, .gr-button-primary { font-weight: 700 !important; }
button { transition: background-color .12s, border-color .12s, transform .06s; }
button:active { transform: translateY(1px); }
:focus-visible { outline: 2px solid var(--accent) !important; outline-offset: 2px; }

/* страницы и тексты */
.page-head h1 { font-size: 30px; font-weight: 800; margin: 2px 0 2px; letter-spacing: -.3px; } .page-head p { margin: 0; font-size: 14.5px; }
.crumbs { color: var(--muted); font-size: 13px; margin-bottom: 4px; }
.note { border: 1px solid rgba(58,195,255,.28); background: rgba(58,195,255,.07); color: #b9dcef; border-radius: 10px; padding: 9px 13px; font-size: 13px; margin-top: 10px; }
.note.warn { border-color: rgba(245,184,61,.4); background: rgba(245,184,61,.08); color: #f6dba4; }
.section-h { font-size: 18px; margin: 8px 0 -4px; }
h3 { font-size: 15px; font-weight: 700; margin: 0; } h4 { font-size: 13px; color: var(--muted); text-transform: none; font-weight: 700; margin: 16px 0 6px; }

/* загрузка выписки */
.upload-card { gap: 12px !important; } .upload-grid { gap: 22px !important; align-items: stretch !important; flex-wrap: wrap !important; }
.upload-grid > .column { gap: 8px !important; } .upload-side { justify-content: flex-start; }
.step { display: flex; align-items: center; gap: 10px; font-weight: 700; font-size: 14px; margin-top: 2px; }
.step-n { width: 24px; height: 24px; border-radius: 50%; display: grid; place-items: center; flex: none; font-size: 13px; background: rgba(37,227,165,.18); color: var(--accent); border: 1px solid rgba(37,227,165,.5); }
.up-hint { margin: 0 !important; line-height: 1.45; }
.upload-side button.lg { height: 48px; font-size: 15px; } .upload-side .step:nth-of-type(n+2) { margin-top: 8px; }
/* зона перетаскивания: русские подписи вместо стандартных английских */
#up-files { border: 0 !important; padding: 0 !important; }
#up-files button.center { border: 2px dashed rgba(37,227,165,.55) !important; border-radius: 14px !important; background: rgba(37,227,165,.045) !important;
  display: flex !important; flex-direction: column; align-items: center; justify-content: center; gap: 10px; position: relative; transition: background-color .15s, border-color .15s; }
#up-files button.center:hover, #up-files button.center:focus-visible { background: rgba(37,227,165,.11) !important; border-color: var(--accent) !important; }
#up-files button.center .wrap { font-size: 0 !important; min-height: 0 !important; height: auto !important; flex: none; display: flex; justify-content: center; }
#up-files button.center { padding: 18px 16px !important; }
#up-files button.center .wrap .icon-wrap { width: 46px !important; height: 46px !important; color: var(--accent); }
#up-files button.center .wrap .or { display: none; }
#up-files button.center::after { content: "Перетащите файл выписки сюда\\A или нажмите, чтобы выбрать на компьютере"; white-space: pre-line; text-align: center; font-size: 15px; font-weight: 600; line-height: 1.5; color: var(--text); }
/* таблица операций: обычный HTML, лёгкая отрисовка */
.ops-wrap { overflow-x: auto; border: 1px solid var(--line); border-radius: 12px; background: rgba(10,24,34,.6); }
table.ops { width: 100%; border-collapse: collapse; font-size: 13px; min-width: 760px; }
table.ops th { text-align: left; color: var(--muted); font-weight: 700; padding: 10px 10px; background: #0c2130; border-bottom: 1px solid var(--line); white-space: nowrap; }
table.ops td { padding: 8px 10px; border-bottom: 1px solid rgba(255,255,255,.05); vertical-align: middle; }
table.ops tr[data-n] { cursor: pointer; } table.ops tbody tr:hover, table.ops tbody tr:focus-visible { background: rgba(37,227,165,.08); outline: none; }
table.ops .n { color: var(--muted); width: 48px; } table.ops .nw { white-space: nowrap; } table.ops .num { text-align: right; font-variant-numeric: tabular-nums; }
table.ops .cp { max-width: 220px; } table.ops .pur { max-width: 420px; } table.ops .ot { max-width: 200px; color: #b8cad5; }
table.ops .cp, table.ops .pur, table.ops .ot { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
table.ops .rk { text-align: center; font-weight: 800; font-size: 15px; width: 84px; }
table.ops .r1 { color: #ff9a9a; background: rgba(255,107,107,.16); } table.ops .r0 { color: #4cf0b6; }
/* ряды карточек одной высоты и без «раскрытия всех сразу» */
.eq-row { align-items: stretch !important; } .eq-row > .column { display: flex; flex-direction: column; } .eq-row .block { flex: 1; }
.eq-row .html-container, .eq-row .prose { height: 100%; } .eq-row .card { height: 100%; box-sizing: border-box; }
.routes { align-items: start; margin-top: 4px; } .routes .card { align-self: start; }
.page-head { margin-bottom: 4px; }
.btn-row { gap: 10px !important; flex-wrap: wrap !important; } #main .btn-row button.lg, #main .btn-row button.md { white-space: normal; height: auto; min-height: 40px; line-height: 1.25; padding-top: 8px; padding-bottom: 8px; }
/* HTML-блоки вровень с таблицами и полями: убираем внутренний отступ Gradio */
.html-container.padding, .html-container { padding: 0 !important; }
/* карточки */
.card { background: linear-gradient(180deg, rgba(18,41,58,.82), rgba(14,32,43,.92)); border: 1px solid var(--line); border-radius: 14px; padding: 16px 18px; box-shadow: 0 2px 8px rgba(0,0,0,.16); }
.card-head { display: flex; align-items: center; gap: 9px; margin-bottom: 12px; color: var(--accent); } .card-head h3 { color: var(--text); } .card-right { margin-left: auto; }
.kpi-row { display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 12px; }
.kpi-row.n5 { grid-template-columns: repeat(5, minmax(0, 1fr)); } .kpi-row.n4 { grid-template-columns: repeat(4, minmax(0, 1fr)); } .kpi-row.n3 { grid-template-columns: repeat(3, minmax(0, 1fr)); }
.kpi { container-type: inline-size; }
@container (max-width: 215px) { .kpi-spark { display: none; } .kpi-ic { width: 38px; height: 38px; } .kpi-value { font-size: 23px; } }
.kpi { display: flex; align-items: center; gap: 13px; background: linear-gradient(180deg, rgba(18,41,58,.85), rgba(14,32,43,.95)); border: 1px solid var(--line); border-radius: 14px; padding: 15px 16px; }
.kpi-ic { width: 46px; height: 46px; border-radius: 12px; display: grid; place-items: center; flex: none; }
.kpi-body { min-width: 0; flex: 1; } .kpi-label { color: var(--muted); font-size: 12.5px; font-weight: 600; } .kpi-value { font-size: 27px; font-weight: 800; line-height: 1.15; font-variant-numeric: tabular-nums; }
.kpi-sub { font-size: 12px; font-weight: 700; margin-top: 1px; } .kpi-spark { align-self: flex-end; opacity: .9; }
.tone-blue .kpi-ic { background: rgba(58,195,255,.16); color: var(--blue); } .tone-green .kpi-ic { background: rgba(37,227,165,.16); color: var(--accent); }
.tone-red .kpi-ic { background: rgba(255,107,107,.17); color: var(--red); } .tone-amber .kpi-ic { background: rgba(245,184,61,.17); color: var(--amber); }
.tone-gray .kpi-ic { background: rgba(139,152,169,.18); color: #b7c3d1; }
.tone-green .kpi-sub { color: var(--accent); } .tone-red .kpi-sub { color: var(--red); } .tone-amber .kpi-sub { color: var(--amber); } .tone-gray .kpi-sub { color: var(--gray); } .tone-blue .kpi-sub { color: var(--blue); }

/* чипы */
.chip { display: inline-flex; align-items: center; gap: 7px; padding: 4px 11px; border-radius: 99px; font-size: 12px; font-weight: 700; white-space: nowrap; border: 1px solid transparent; }
.chip .dot { width: 7px; height: 7px; border-radius: 50%; background: currentColor; }
.chip-disputable, .chip-job-bad { color: #ff9a9a; background: rgba(255,107,107,.13); border-color: rgba(255,107,107,.4); }
.chip-attention, .chip-job-warn { color: #f8cf7a; background: rgba(245,184,61,.13); border-color: rgba(245,184,61,.4); }
.chip-unclassified, .chip-job-queued { color: #b7c3d1; background: rgba(139,152,169,.14); border-color: rgba(139,152,169,.38); }
.chip-typical, .chip-job-ok { color: #4cf0b6; background: rgba(37,227,165,.13); border-color: rgba(37,227,165,.4); }
.chip-job-run { color: #7fd6ff; background: rgba(58,195,255,.13); border-color: rgba(58,195,255,.4); }

/* графики */
.donut-wrap { display: flex; align-items: center; gap: 18px; flex-wrap: wrap; } .donut { flex: none; } .donut-v { fill: var(--text); font-size: 21px; font-weight: 800; } .donut-l { fill: var(--muted); font-size: 9.5px; }
.legend { list-style: none; margin: 0; padding: 0; flex: 1; min-width: 190px; display: grid; gap: 7px; }
.legend li { display: grid; grid-template-columns: 12px 1fr auto 52px; gap: 8px; align-items: center; font-size: 13px; }
.sw { width: 10px; height: 10px; border-radius: 3px; display: inline-block; } .lg-n { color: var(--muted); } .lg-p { text-align: right; font-weight: 700; }
.bars { width: 100%; height: auto; } .bars .gl { stroke: rgba(255,255,255,.07); } .bars .ax { fill: var(--muted); font-size: 10.5px; }
.legend-inline { display: flex; gap: 16px; font-size: 12.5px; margin-top: 4px; } .legend-inline .sw { margin-right: 6px; }
.columns { display: flex; align-items: flex-end; gap: 14px; height: 200px; padding-top: 8px; } .col { flex: 1; display: flex; flex-direction: column; align-items: center; justify-content: flex-end; gap: 5px; height: 100%; }
.col-bar { width: 62%; max-width: 54px; border-radius: 6px 6px 2px 2px; background: linear-gradient(180deg, #4af0b9, #12b886); } .col-v { font-weight: 800; font-size: 13px; } .col-l { color: var(--muted); font-size: 11.5px; text-align: center; min-height: 28px; }
.cf-row { display: flex; align-items: center; gap: 10px; padding: 11px 0; border-bottom: 1px solid var(--line); } .cf-row:last-child { border: 0; }
.cf-ic { width: 26px; height: 26px; border-radius: 8px; display: grid; place-items: center; background: rgba(255,255,255,.05); } .cf-l { flex: 1; color: #c6d6df; font-size: 13.5px; } .cf-v { font-weight: 800; font-variant-numeric: tabular-nums; }
.tone-pos .cf-ic, .tone-pos .cf-v { color: var(--accent); } .tone-neg .cf-ic, .tone-neg .cf-v { color: var(--red); }
.hbars { display: grid; gap: 11px; } .hb { display: grid; grid-template-columns: 22px minmax(140px, 1.3fr) 2fr 78px 52px; gap: 10px; align-items: center; font-size: 13.5px; }
.hb-n { color: var(--muted); font-weight: 700; } .hb-track { height: 9px; border-radius: 99px; background: rgba(255,255,255,.07); overflow: hidden; } .hb-fill { display: block; height: 100%; border-radius: 99px; background: linear-gradient(90deg, #12b886, #4af0b9); }
.hb-v { text-align: right; font-weight: 700; font-variant-numeric: tabular-nums; } .hb-p { text-align: right; color: var(--muted); }
.hb-name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

/* очередь и загрузки */
.job, .recent { display: flex; align-items: center; gap: 14px; padding: 10px 0; } .job + .job { border-top: 1px solid var(--line); }
.job-ic { width: 38px; height: 38px; border-radius: 10px; background: rgba(58,195,255,.12); color: var(--blue); display: grid; place-items: center; flex: none; }
.job-main { flex: 1; min-width: 0; } .job-title { font-weight: 700; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; } .job-msg { color: var(--muted); font-size: 12.5px; margin: 1px 0 6px; }
.job-side { display: flex; flex-direction: column; align-items: flex-end; gap: 5px; min-width: 130px; } .job-pct { font-weight: 800; color: var(--blue); font-size: 13px; }
.bar { height: 7px; border-radius: 99px; background: rgba(255,255,255,.08); overflow: hidden; width: 100%; } .bar.slim { width: 130px; height: 5px; } .bar-fill { height: 100%; background: linear-gradient(90deg, #3ac3ff, #25e3a5); border-radius: 99px; transition: width .4s; }
.recent-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 14px; } .recent { border: 1px solid var(--line); border-radius: 12px; padding: 12px; background: rgba(255,255,255,.02); }

/* карточка операции */
.status-banner { display: flex; align-items: center; gap: 9px; border-radius: 10px; padding: 9px 13px; font-weight: 700; font-size: 13.5px; border: 1px solid; margin-bottom: 12px; }
.st-disputable { color: #ff9a9a; background: rgba(255,107,107,.14); border-color: rgba(255,107,107,.45); } .st-attention { color: #f8cf7a; background: rgba(245,184,61,.13); border-color: rgba(245,184,61,.45); }
.st-unclassified { color: #b7c3d1; background: rgba(139,152,169,.13); border-color: rgba(139,152,169,.4); } .st-typical { color: #4cf0b6; background: rgba(37,227,165,.12); border-color: rgba(37,227,165,.4); }
.amount { font-size: 34px; font-weight: 800; letter-spacing: -.5px; } .dir { display: flex; gap: 6px; align-items: center; font-weight: 700; margin-bottom: 8px; }
.kvs { margin-top: 6px; } .kv { display: grid; grid-template-columns: 150px 1fr; gap: 12px; padding: 9px 0; border-top: 1px solid var(--line); font-size: 13.5px; } .kv span { color: var(--muted); } .kv b { font-weight: 600; word-break: break-word; }
.review-badge { display: inline-block; font-size: 12px; color: var(--accent); border: 1px solid rgba(37,227,165,.4); border-radius: 99px; padding: 2px 10px; margin-bottom: 6px; }
.ground-box { border: 1px solid rgba(255,107,107,.45); background: linear-gradient(180deg, rgba(255,107,107,.16), rgba(255,107,107,.06)); border-radius: 12px; padding: 14px 16px; }
.ground-box.calm { border-color: rgba(37,227,165,.35); background: linear-gradient(180deg, rgba(37,227,165,.12), rgba(37,227,165,.04)); }
.ground-k { font-size: 12px; color: #ffb0b0; font-weight: 700; } .calm .ground-k { color: #7cf2c6; } .ground-t { font-size: 22px; font-weight: 800; margin: 3px 0; } .ground-t.sm { font-size: 18px; color: var(--accent); } .ground-s { font-weight: 600; color: #e3eef3; } .ground-box p { margin: 8px 0 0; color: #d0dde5; font-size: 13.5px; }
.factors { display: grid; gap: 8px; } .factor { display: flex; align-items: center; gap: 11px; background: rgba(58,195,255,.07); border: 1px solid rgba(58,195,255,.2); border-radius: 10px; padding: 10px 12px; font-size: 13.5px; } .f-ic { color: var(--blue); }
blockquote { margin: 6px 0; padding: 8px 12px; border-left: 3px solid var(--line2); background: rgba(255,255,255,.03); border-radius: 0 8px 8px 0; font-size: 13px; color: #c9d8e1; }
.op-ground p, .op-docs p { font-size: 13.5px; line-height: 1.5; margin: 0 0 4px; } .src { margin-top: 12px; font-size: 12px; }
.doclist { margin: 0; padding-left: 18px; display: grid; gap: 5px; font-size: 13.5px; } .doclist.warn { color: #f6dba4; }
table.mini { width: 100%; border-collapse: collapse; font-size: 13px; } table.mini th { text-align: left; color: var(--muted); font-weight: 600; padding: 6px 8px; border-bottom: 1px solid var(--line); }
table.mini td { padding: 8px; border-bottom: 1px solid rgba(255,255,255,.05); vertical-align: top; } .pur { max-width: 260px; }
.card-body { overflow-x: auto; } .sdot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 7px; vertical-align: middle; }
.cp-head { display: flex; justify-content: space-between; gap: 12px; align-items: flex-start; margin-bottom: 10px; } .stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 10px; margin-bottom: 12px; }
.stat { background: rgba(255,255,255,.03); border: 1px solid var(--line); border-radius: 10px; padding: 9px 11px; } .stat span { display: block; color: var(--muted); font-size: 12px; } .stat b { font-size: 15px; }
.routes { display: grid; grid-template-columns: repeat(auto-fit, minmax(380px, 1fr)); gap: 14px; } .route-top { display: flex; justify-content: space-between; gap: 14px; margin-bottom: 8px; }
.route-nums { display: grid; grid-template-columns: auto; text-align: right; } .route-nums b { font-size: 20px; } .route-nums span { color: var(--muted); font-size: 12px; margin-bottom: 4px; }
details summary { cursor: pointer; color: var(--accent); font-weight: 600; margin-top: 10px; font-size: 13.5px; } details[open] summary { margin-bottom: 6px; }
.empty { text-align: center; padding: 46px 20px; border: 1px dashed var(--line2); border-radius: 16px; background: rgba(255,255,255,.02); } .empty-ic { color: var(--accent); margin-bottom: 8px; } .empty h3 { font-size: 18px; margin-bottom: 4px; }

/* компоненты Gradio */
.block.padded, .form { background: transparent !important; } .gr-group, .group { background: transparent !important; border: 0 !important; }
label > span, .block label span { font-size: 12.5px !important; font-weight: 600; }
.ctrl-card { background: linear-gradient(180deg, rgba(18,41,58,.82), rgba(14,32,43,.92)); border: 1px solid var(--line); border-radius: 14px; padding: 14px 16px !important; gap: 10px !important; }
.filters { gap: 10px !important; align-items: flex-end !important; flex-wrap: wrap !important; } .filters button { height: 40px; } .filters > * { min-width: 120px; }
#pages > .tab-wrapper, #pages > .tab-nav, #pages > div > .tab-nav { display: none !important; } #pages { border: 0 !important; } #pages .tabitem { padding: 0 !important; border: 0 !important; background: transparent !important; }
.tabitem > .column, .tabitem > div { gap: 14px !important; }
button.secondary, button.lg, button.sm { border-radius: 10px !important; font-weight: 600 !important; }
.table-wrap, .dataframe, div[data-testid="dataframe"] { border: 1px solid var(--line) !important; border-radius: 12px !important; overflow: hidden; }
.table-wrap td, .table-wrap th, .table-wrap span, .table-wrap input { font-size: 13px; font-family: var(--font) !important; } .table-wrap th { background: #0c2130 !important; color: var(--muted) !important; font-weight: 700; }
.main-btn-fix {} #main button.lg, #main button.md { white-space: nowrap; min-height: 40px; }
.pager { display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; } .pager-info { color: var(--muted); font-size: 13px; }
.seg { border: 0 !important; background: transparent !important; } .seg .wrap { gap: 8px !important; }
.seg label { border: 1px solid var(--line2) !important; border-radius: 99px !important; padding: 5px 14px !important; background: rgba(255,255,255,.03) !important; cursor: pointer; }
.seg label.selected { background: rgba(37,227,165,.16) !important; border-color: var(--accent) !important; color: #eafff7 !important; }
.seg input[type=radio] { display: none; }
.toast-wrap { z-index: 9999; }
@media (max-width: 900px) {
  #shell { flex-direction: column !important; height: auto; overflow: visible; }
  #sidebar { max-width: 100% !important; min-width: 100% !important; width: 100% !important; height: auto !important; flex-direction: row !important;
    overflow-x: auto; overflow-y: hidden; padding: 8px 10px !important; align-items: center; gap: 6px !important; position: sticky; top: 0; z-index: 20; }
  #sidebar > * { flex: none !important; width: auto !important; min-width: 0 !important; }
  #sidebar button.nav { min-height: 38px !important; height: 38px !important; padding: 0 14px !important; white-space: nowrap; }
  .brand { padding: 0 8px 0 0; } .brand-sub, #side-foot { display: none !important; }
  #main { height: auto; overflow: visible; padding: 12px 14px 32px !important; }
  .kv { grid-template-columns: 110px 1fr; } .hb { grid-template-columns: 20px 1fr 2fr 70px; } .hb-p { display: none; }
  .page-head h1 { font-size: 24px; } .routes { grid-template-columns: 1fr; } .donut-wrap { justify-content: center; }
}
@media (max-width: 1100px) { .kpi-row.n5, .kpi-row.n4 { grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); } }
/* ноутбуки: 1280–1440 px и ниже по высоте */
@media (max-width: 1440px) {
  #sidebar { min-width: 212px !important; max-width: 212px !important; padding: 14px 10px !important; }
  #main { padding: 14px 20px 32px !important; gap: 12px !important; }
  .page-head h1 { font-size: 26px; } .card { padding: 14px 15px; } .kpi { padding: 12px 13px; gap: 11px; }
  .kpi-value { font-size: 24px; } .amount { font-size: 30px; } .kv { grid-template-columns: 130px 1fr; }
  #sidebar button.nav { padding: 9px 12px !important; min-height: 40px; font-size: 14px; }
  .ctrl-card { padding: 12px 14px !important; } .routes { grid-template-columns: repeat(auto-fit, minmax(340px, 1fr)); }
  .table-wrap td, .table-wrap th { font-size: 12.5px; }
}
@media (max-height: 760px) and (min-width: 901px) { .brand { padding-bottom: 8px; } #sidebar button.nav { min-height: 36px; padding: 7px 12px !important; } .side-status { font-size: 11.5px; padding: 8px 10px; } }
@media (prefers-reduced-motion: reduce) { * { transition: none !important; animation: none !important; } }
"""
