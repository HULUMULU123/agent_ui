"""Сервис парсера, хранилище, очередь задач, аналитика, экспорт и сборка интерфейса."""
from __future__ import annotations

import importlib
import time
from pathlib import Path

import pandas as pd
import pytest

DATA = Path(__file__).parent / "data"


@pytest.fixture()
def app_modules(tmp_path, monkeypatch):
    """Изолированная папка данных и память риска; модули перезагружаются, чтобы подхватить переменные окружения."""
    monkeypatch.setenv("FINAPP_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("RISK_MEMORY_DB_PATH", str(tmp_path / "risk.db"))
    for name in ("GIGACHAT_BASE_URL", "GIGACHAT_CREDENTIALS"):
        monkeypatch.delenv(name, raising=False)
    import finapp.config, finapp.store, finapp.factory, finapp.jobs, finapp.ui.app  # noqa: E401
    for module in (finapp.config, finapp.store, finapp.factory, finapp.jobs, finapp.ui.app):
        importlib.reload(module)
    return finapp.store, finapp.jobs


def wait_done(store, analysis_id, timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        meta = store.STORE.get_meta(analysis_id)
        if meta["status"] in ("done", "error", "cancelled"):
            return meta
        time.sleep(0.2)
    raise TimeoutError


@pytest.mark.parametrize("name", ["c01_basic.xlsx", "c16_csv_cp1251.csv", "c17_1c.txt", "c13_card_individual.xlsx"])
def test_parser_service_reads_various_formats(name):
    from finapp.parser.service import parse_statement_file
    outcome = parse_statement_file(DATA / name)
    assert len(outcome.table) >= 8 and list(outcome.table.columns) == [
        "date", "debit_inn", "debit_name", "credit_inn", "credit_name", "amount", "purpose"]
    assert outcome.status in ("ok", "ok_unverified", "needs_review")


def test_parser_service_rejects_unknown_format(tmp_path):
    from finapp.parser.service import parse_statement_file
    bad = tmp_path / "x.exe"
    bad.write_bytes(b"MZ")
    with pytest.raises(ValueError, match="не поддерживается"):
        parse_statement_file(bad)


def test_ready_table_skips_parser_and_finds_owner():
    from finapp.parser.service import guess_owner
    from tools.make_demo_statement import build
    table = build()
    assert guess_owner(table) == ("7712345675", "ООО «Торговый Дом Вектор»")
    n = len(table)
    scattered = table.assign(debit_inn=[str(10_000 + i) for i in range(n)], credit_inn=[str(20_000 + i) for i in range(n)])
    assert guess_owner(scattered) == ("", "")  # единого владельца нет — не угадываем


def test_end_to_end_job_store_and_exports(app_modules, tmp_path):
    store, jobs = app_modules
    meta = jobs.JOBS.submit(DATA / "c01_basic.xlsx", "01.06.2024")
    done = wait_done(store, meta["id"])
    assert done["status"] == "done", done.get("error")
    assert done["mode"] == "rules" and done["org_inn"] and done["n_ops"] == 15
    bundle = store.STORE.load(meta["id"])
    assert {"status", "cp_inn", "ground", "operation_category"} <= set(bundle.df.columns)

    from finapp.analytics import OperationFilters, apply_filters, counterparty_table, grounds_summary, kpis
    k = kpis(bundle.df)
    assert k["total"] == 15 and k["disputable"] + k["typical"] == 15  # только риск 1 и риск 0
    assert not counterparty_table(bundle.df).empty
    only_risky = apply_filters(bundle.df, OperationFilters(risk=1))
    assert (only_risky["risk_level"] == 1).all() and (only_risky["status"] == "disputable").all()
    assert apply_filters(bundle.df, OperationFilters(query="займа")).shape[0] > 0
    assert grounds_summary(bundle.df).empty or "ground" in grounds_summary(bundle.df).columns

    from finapp.export import export_workbook
    path = export_workbook(bundle.df, tmp_path / "out.xlsx", only_idx=set(bundle.df["idx"].head(3)))
    sheets = pd.read_excel(path, sheet_name=None)
    assert set(sheets) == {"Выписка", "Контрагенты", "Статистика по выписке"} and len(sheets["Выписка"]) == 3


def test_job_error_is_reported_not_raised(app_modules, tmp_path):
    store, jobs = app_modules
    empty = tmp_path / "empty.csv"
    empty.write_text("a,b\n1,2\n", encoding="utf-8")
    meta = jobs.JOBS.submit(empty, "01.06.2024")
    done = wait_done(store, meta["id"])
    assert done["status"] == "error" and done["error"]


def test_submit_validates_date(app_modules):
    _, jobs = app_modules
    with pytest.raises(ValueError):
        jobs.JOBS.submit(DATA / "c01_basic.xlsx", "вчера")


def test_reviews_roundtrip_and_report_flag(app_modules):
    store, jobs = app_modules
    meta = jobs.JOBS.submit(DATA / "c01_basic.xlsx", "01.06.2024")
    wait_done(store, meta["id"])
    s = store.STORE
    s.save_review(meta["id"], "abc", "confirmed", "проверено", ["пункт 1"])
    s.set_in_report(meta["id"], "abc", True)
    review = s.load_reviews(meta["id"])["abc"]
    assert review["decision"] == "confirmed" and review["in_report"] and review["checked"] == ["пункт 1"]
    s.save_review(meta["id"], "abc", "dismissed", "", [])  # in_report сохраняется
    assert s.load_reviews(meta["id"])["abc"]["in_report"] is True
    s.set_in_report(meta["id"], "abc", False)
    s.save_review(meta["id"], "abc", "", "", [])
    assert "abc" not in s.load_reviews(meta["id"])
    with pytest.raises(ValueError):
        s.save_review(meta["id"], "abc", "bogus", "", [])


def test_interrupted_jobs_are_marked_on_restart(app_modules):
    store, _ = app_modules
    meta = store.STORE.create("x.xlsx", "01.06.2024")
    assert store.STORE.mark_interrupted() == 1
    assert store.STORE.get_meta(meta["id"])["status"] == "error"


def test_store_rejects_path_traversal(app_modules):
    store, _ = app_modules
    with pytest.raises(ValueError):
        store.STORE.dir("../etc")


def test_ui_builds(app_modules):
    from finapp.ui.app import build_app
    demo = build_app()
    assert demo is not None


def test_recommendation_editing_roundtrip_and_export(app_modules, tmp_path):
    """Правка рекомендации сотрудником: сохраняется, видна в таблицах, обе версии уходят в Excel, возврат к исходной работает."""
    store, jobs = app_modules
    from tools.make_demo_statement import build
    demo = tmp_path / "demo.xlsx"
    build().to_excel(demo, index=False)
    meta = jobs.JOBS.submit(demo, "10.10.2025")
    assert wait_done(store, meta["id"])["status"] == "done"

    from finapp.ui import views as V
    from finapp.ui.app import context_for
    from finapp.export import export_workbook

    ctx = context_for(meta["id"])
    table, info, pages = V.recommendations_page(ctx, V.REC_SCOPES[0], "", 0, 15)
    assert len(table) >= 3 and "изменено сотрудником: 0" in info
    original = table.iloc[0]["Рекомендация (можно править)"]

    edited = table.copy()
    edited.loc[0, "Рекомендация (можно править)"] = "Запросить график платежей и реестр кредиторов"
    saved, reset = V.save_recommendation_edits(ctx, V.REC_SCOPES[0], "", 0, 15, edited, store.STORE)
    assert (saved, reset) == (1, 0)

    ctx = context_for(meta["id"])
    table2, info2, _ = V.recommendations_page(ctx, V.REC_SCOPES[0], "", 0, 15)
    assert table2.iloc[0]["Рекомендация (можно править)"] == "Запросить график платежей и реестр кредиторов"
    assert "изменено сотрудником: 1" in info2
    assert V.recommendations_page(ctx, "Только с правками", "", 0, 15)[0].shape[0] == 1

    path = export_workbook(ctx.df, tmp_path / "r.xlsx", reviews=ctx.reviews)
    sheet = pd.read_excel(path, sheet_name="Выписка")
    changed = sheet[sheet["Рекомендация агента (до правки)"].notna()]
    assert len(changed) == 1 and changed.iloc[0]["Рекомендация"] == "Запросить график платежей и реестр кредиторов"
    assert changed.iloc[0]["Рекомендация агента (до правки)"].startswith(original[:30])

    # сохранение без изменений ничего не делает; возврат исходного текста снимает правку
    assert V.save_recommendation_edits(ctx, V.REC_SCOPES[0], "", 0, 15, table2, store.STORE) == (0, 0)
    back = table2.copy()
    back.loc[0, "Рекомендация (можно править)"] = original
    assert V.save_recommendation_edits(ctx, V.REC_SCOPES[0], "", 0, 15, back, store.STORE) == (0, 1)
    assert not any(r.get("recommendation") for r in store.STORE.load_reviews(meta["id"]).values())


def test_operations_tab_uses_risk_0_1_and_light_html_table(app_modules, tmp_path):
    store, jobs = app_modules
    from tools.make_demo_statement import build
    demo = tmp_path / "demo.xlsx"
    build().to_excel(demo, index=False)
    meta = jobs.JOBS.submit(demo, "10.10.2025")
    assert wait_done(store, meta["id"])["status"] == "done"

    from finapp.ui import render as R, views as V
    from finapp.ui.app import context_for
    ctx = context_for(meta["id"])
    assert set(ctx.df["risk_level"]) <= {0, 1} and set(ctx.df["status_label"]) <= {"Требует проверки", "Риска нет"}

    rows, info, pages, filtered = V.operations_rows(ctx, V.make_filters("", "Все", "Все"), 0)
    assert len(rows) == V.OPS_PAGE_SIZE and pages > 1
    assert [r["risk"] for r in rows[:6]] == [1] * 6  # сначала риск 1
    assert all(r["risk"] in (0, 1) for r in rows)
    html = R.ops_table_html(rows)
    assert html.count("<tr data-n=") == len(rows) and "Риск (0/1)" in html and "Потенциально" not in html

    only1, *_ = V.operations_rows(ctx, V.make_filters("", "Риск 1", "Все"), 0)
    assert len(only1) == 6 and {r["risk"] for r in only1} == {1}
    only0, _, _, f0 = V.operations_rows(ctx, V.make_filters("", "Риск 0", "Поступление"), 0)
    assert {r["risk"] for r in only0} == {0} and (f0["direction"] == "прибытие").all()
    found, _, _, ff = V.operations_rows(ctx, V.make_filters("займ", "Все", "Все"), 0)
    assert found and ff["purpose"].str.lower().str.contains("займ").all()
    assert "<script" not in R.ops_table_html([{**rows[0], "purpose": "<script>x</script>"}])  # экранирование
