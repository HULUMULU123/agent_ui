"""Агент: подготовка данных, классификатор, граф с эмуляцией GigaChat, перенос, повторные проходы, память риска."""
from __future__ import annotations

import sqlite3
from datetime import date

import numpy as np
import pandas as pd
import pytest

from fake_llm import FakeLLM
from finapp.agent.classifier import classify_by_rules, keyword_subject
from finapp.agent.common import AgentConfig, normalize_inn, parse_filing_date, resolve_counterparty_name, sanitize_text
from finapp.agent.dataprep import days_to_period, prepare_statement
from finapp.agent.graph import finalize_operation
from finapp.agent.common import DataProfile
from finapp.agent.pipeline import run_analysis
from finapp.agent.risk_memory import RiskMemoryDB
from finapp.agent.runtime import Runtime
from finapp.agent.similarity import SimilaritySpace

FILING = date(2024, 6, 1)


def statement(n_repeat: int = 1) -> pd.DataFrame:
    rows = []
    for k in range(n_repeat):
        base = pd.Timestamp("2024-04-01") + pd.Timedelta(days=k)
        rows += [
            (base, "Заработная плата за март", -500_000, "7700000001", "ООО Банк"),
            (base, "Возврат займа по договору займа № 5", -900_000, "7700000002", "ООО Финанс"),
            (base, "Оплата по договору поставки № 12", -300_000, "7700000003", "ООО Поставщик"),
            (base, "Оплата за товар по счёту № 7", 700_000, "7700000004", "ООО Покупатель"),
            (base - pd.Timedelta(days=1500), "Оплата услуг связи", -20_000, "7700000005", "ПАО Связь"),
        ]
    return pd.DataFrame([{"date": d, "debit_inn": "7712345675" if a < 0 else i, "debit_name": "ООО Должник" if a < 0 else n,
                          "credit_inn": i if a < 0 else "7712345675", "credit_name": n if a < 0 else "ООО Должник",
                          "amount": float(a), "purpose": p} for d, p, a, i, n in rows])


@pytest.fixture()
def runtime(tmp_path):
    return Runtime(cfg=AgentConfig(court_filing_date=FILING, llm_max_workers=2), llm=FakeLLM(), llm_helper=FakeLLM(),
                   risk_db=RiskMemoryDB(tmp_path / "risk.db"))


def test_normalizers():
    assert normalize_inn(7712345675.0) == "7712345675"
    assert normalize_inn("771234567") == "0771234567"  # потерянный ведущий ноль
    assert normalize_inn(pd.NA) == "" and normalize_inn(None) == "" and normalize_inn("nan") == ""
    assert parse_filing_date("10.10.2025") == date(2025, 10, 10)
    with pytest.raises(ValueError):
        parse_filing_date("32.13.2025")


def test_period_boundaries():
    days = pd.Series([5, 0, -10, -31, -183, -366, -1096, np.nan])
    assert days_to_period(days).tolist() == [
        "после подачи заявления", "после подачи заявления", "менее 1 месяца до заявления", "1–6 месяцев до заявления",
        "6–12 месяцев до заявления", "1–3 года до заявления", "более 3 лет до заявления", "не определено"]


def test_parser_output_with_nullable_dtypes_does_not_crash():
    """Парсер отдаёт string/Float64 с pd.NA — в ноутбуке `value or ""` на pd.NA падало."""
    raw = statement().astype({"debit_inn": "string", "credit_inn": "string", "debit_name": "string", "credit_name": "string",
                              "purpose": "string", "amount": "Float64"})
    raw.loc[0, "credit_name"] = pd.NA
    prepared = prepare_statement(raw, AgentConfig(court_filing_date=FILING))
    assert len(prepared.df) == len(raw)
    row = prepared.df[prepared.df["purpose"] == raw.loc[0, "purpose"]].iloc[0].to_dict()
    assert resolve_counterparty_name(row) is None  # имя отсутствует (pd.NA) — без исключения


def test_direction_follows_amount_sign():
    prepared = prepare_statement(statement(), AgentConfig(court_filing_date=FILING))
    by_purpose = prepared.df.set_index("purpose")["direction"].to_dict()
    assert by_purpose["Оплата за товар по счёту № 7"] == "прибытие"
    assert by_purpose["Возврат займа по договору займа № 5"] == "убытие"


def test_rejects_rows_without_required_fields():
    raw = statement()
    raw.loc[1, "purpose"] = ""
    raw.loc[2, "date"] = pd.NaT
    prepared = prepare_statement(raw, AgentConfig(court_filing_date=FILING))
    assert len(prepared.df) == len(raw) - 2 and len(prepared.rejected) == 2


def test_rule_classifier_and_unknown_types():
    assert keyword_subject("Оплата по договору № 55") is None
    assert keyword_subject("Снятие наличных на хозяйственные нужды") == "cash_withdrawal"
    frame = pd.DataFrame({"idx": ["1", "2"], "purpose": ["Комиссия за расчётно-кассовое обслуживание", "Перечисление"],
                          "direction": ["убытие", "убытие"]})
    out = classify_by_rules(frame)
    assert out.loc[0, "operation_type"] == "Комиссия банка за РКО"
    assert out.loc[1, "operation_category"] == "Не классифицировано"


def test_full_pipeline_with_llm_covers_every_operation(runtime):
    result = run_analysis(statement(), runtime)
    df = result.full_analysis
    assert len(df) == 5 and df["risk_level"].isin([0, 1]).all()
    risky = df[df["risk_level"] == 1]
    assert list(risky["purpose"]) == ["Возврат займа по договору займа № 5"]
    assert risky.iloc[0]["check_route"] == "preference" and "61.3" in risky.iloc[0]["legal_qualification"]
    old = df[df["purpose"] == "Оплата услуг связи"].iloc[0]
    assert old["analysis_source"] == "rule" and old["risk_level"] == 0  # старше 3 лет — по правилу, без LLM
    assert result.memory_update["written"] == 1
    assert runtime.risk_db.compute_current_risk("7700000002")["events_count"] == 1


def test_llm_sees_no_real_inn_or_service_fields(runtime):
    seen: list[str] = []
    original = runtime.llm.invoke

    def spy(messages):
        seen.append(str(messages[-1][1] if isinstance(messages[-1], tuple) else messages[-1].content))
        return original(messages)

    runtime.llm.invoke = spy
    run_analysis(statement(), runtime)
    joined = "\n".join(seen)
    assert "7700000002" not in joined and "ORG_" in joined
    assert "purpose_cleaned" not in joined and "idx" not in joined


def test_propagation_and_batch_dedup(runtime):
    result = run_analysis(statement(n_repeat=12), runtime)
    df = result.full_analysis
    assert len(df) == 60 and not df["risk_level"].isna().any()
    assert (df["analysis_source"] == "propagated").any()
    propagated = df[df["analysis_source"] == "propagated"]
    assert propagated["matched_representative_idx"].notna().all() and propagated["similarity_score"].ge(0.65).all()


def test_garbage_llm_answers_become_manual_review(tmp_path):
    rt = Runtime(cfg=AgentConfig(court_filing_date=FILING, llm_max_workers=1), llm=FakeLLM(fail_roles={"analyzer"}),
                 llm_helper=FakeLLM(), risk_db=RiskMemoryDB(tmp_path / "r.db"))
    df = run_analysis(statement(), rt).full_analysis
    live = df[df["analysis_source"] != "rule"]
    assert (live["analysis_source"] == "manual_review_required").all() and (live["risk_level"] == 1).all()


def test_rules_mode_without_llm():
    result = run_analysis(statement(), Runtime(cfg=AgentConfig(court_filing_date=FILING)))
    df = result.full_analysis
    assert set(df["analysis_source"]) <= {"rules", "rule"} and df["risk_level"].notna().all()
    assert "Возврат займа по договору займа № 5" in set(df[df["risk_level"] == 1]["purpose"])


def test_local_clustering_scales_beyond_limit():
    cfg = AgentConfig(court_filing_date=FILING, no_cluster_local_clustering_max_ops=50)
    prepared = prepare_statement(statement(n_repeat=40), cfg)
    space = SimilaritySpace(prepared.df, cfg)
    clustered = space.assign_local_clusters(prepared.df)
    assert len(clustered) == 200 and (clustered["cluster"] >= 0).sum() > 100


def test_sanitize_removes_service_data():
    assert "ORG_3" not in sanitize_text("Платёж контрагенту ORG_3 по операции (anomaly_score 0.97)")
    assert "anomaly_score" not in sanitize_text("Высокий anomaly_score 0.97 у платежа")
    assert sanitize_text("H_PREFERENCE подтверждается") == "возможное предпочтение отдельному кредитору подтверждается"


def test_finalize_keeps_risk_level_and_aligns_route():
    profile = DataProfile()
    low = finalize_operation({"risk_level": 0, "check_route": "preference", "risk_factors": ["debt_repayment"]}, {}, False, profile)
    assert low["risk_level"] == 0 and low["check_route"] in ("none", "documents") and low["risk_factors"] == []
    high = finalize_operation({"risk_level": "1", "check_route": "none", "risk_factors": ["transit"]}, {}, False, profile)
    assert high["risk_level"] == 1 and high["check_route"] == "transit"
    assert finalize_operation({"risk_level": 7}, {}, False, profile)["risk_level"] is None
    refs = finalize_operation({"risk_level": 1, "check_route": "preference", "legal_refs": [{"ref": "N1", "application": "ок"}, {"ref": "N9"}]},
                              {"N1": "Статья: текст"}, False, profile)
    assert refs["legal_basis"] == ["Статья: текст\nПрименение к операции: ок"]  # ссылки только на реально найденное


def test_risk_memory_migrates_legacy_schema(tmp_path):
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.executescript("""CREATE TABLE risk_events (id INTEGER PRIMARY KEY AUTOINCREMENT, inn TEXT NOT NULL,
        operation_idx TEXT NOT NULL DEFAULT '', risk_level INTEGER NOT NULL CHECK (risk_level IN (2, 3)), operation_date TEXT NOT NULL,
        court_filing_date TEXT NOT NULL, reason TEXT, created_at TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at TEXT NOT NULL DEFAULT (datetime('now')), UNIQUE (inn, court_filing_date, operation_idx));
        INSERT INTO risk_events (inn, operation_idx, risk_level, operation_date, court_filing_date, reason)
        VALUES ('7700000002', 'a', 3, '2024-04-01', '2024-06-01', 'старое событие'), ('7700000002', 'b', 2, '2024-04-02', '2024-06-01', 'ещё');""")
    conn.commit()
    conn.close()
    db = RiskMemoryDB(path)
    assert db.compute_current_risk("7700000002")["events_count"] == 2
    db.add_risk_event("7700000002", "2024-05-01", "2024-06-01", "новое", "c")
    assert db.compute_current_risk("7700000002")["events_count"] == 3
    assert list(tmp_path.glob("old.db.bak-*"))  # резервная копия создана


def test_gigachat_settings_explain_why_not_configured(tmp_path):
    from finapp.agent.runtime import DEFAULT_BASE_URL, GigaChatSettings
    empty = GigaChatSettings(cert_file=str(tmp_path / "cert.pem"), key_file=str(tmp_path / "private.key"))
    assert empty.effective_base_url == DEFAULT_BASE_URL  # пустой адрес -> корпоративный по умолчанию
    assert not empty.configured and "сертификата" in empty.problem()
    (tmp_path / "cert.pem").write_text("x")
    assert "закрытого ключа" in empty.problem()
    (tmp_path / "private.key").write_text("x")
    assert empty.configured and empty.problem() is None  # сертификаты на месте — подключено без ручного ввода адреса
    by_key = GigaChatSettings(credentials="k", cert_file="нет", key_file="нет")
    assert by_key.configured and not by_key.uses_certificates
