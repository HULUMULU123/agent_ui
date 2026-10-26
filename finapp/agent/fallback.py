"""Предварительная оценка по правилам — режим, когда GigaChat не подключён (или не ответил по пакету).

Правила повторяют логику промпта анализатора в упрощённом виде и намеренно консервативны: тип операции, направление,
период относительно заявления и размер суммы. Связи, профиль контрагента и правовые материалы здесь недоступны, поэтому
результат — ориентир для сортировки, а не замена анализа агентом. Источник помечается «rules».
"""
from __future__ import annotations

import pandas as pd

from .prompts import ROUTES

NOTE = "Предварительная оценка по правилам: GigaChat не подключён, связи, профиль контрагента и судебная практика не использовались."

DEBT_TYPES = {
    "Возврат займа", "Погашение кредита", "Проценты по займу", "Проценты по кредиту", "Отступное", "Взаимозачёт",
    "Реестровый платёж", "Перевод долга", "Уступка права требования", "Новация",
}
NO_COUNTER_PERFORMANCE_TYPES = {
    "Безвозмездная передача / дарение", "Благотворительность", "Подарок", "Предоставление финансовой помощи",
    "Предоставление займа", "Предоставление беспроцентного займа", "Выплата дивидендов", "Снятие наличных",
    "Перевод на банковскую карту", "Перевод на личные нужды собственника", "Прощение долга",
    "Перевод аффилированному лицу", "Выкуп доли у участника",
}
ORDINARY_TYPES_PREFIXES = ("Заработная плата", "Уплата", "Единый налоговый", "Комиссия", "Коммунальные", "Услуги связи",
                           "Выплата", "Отпускные", "Премия", "Аренда", "Страхов", "Плата за", "Открытие")
DOC_CHECK_TYPES = {"Поставка товаров", "Консультационные услуги", "IT-услуги", "Маркетинговые услуги", "Юридические услуги",
                   "Строительный подряд", "Техническое обслуживание и ремонт", "Транспортные услуги", "Агентские и комиссионные услуги"}
RECENT_PERIODS = {"после подачи заявления", "менее 1 месяца до заявления", "1–6 месяцев до заявления"}
LONG_PERIODS = RECENT_PERIODS | {"6–12 месяцев до заявления", "1–3 года до заявления"}


def _result(risk: int, route: str, factors: list[str], argument: str, explanation: str, recommendation: str,
            docs: list[str], category: str) -> dict:
    from .common import normalize_to_list  # noqa: F401  (локальный импорт: избегаем циклов при загрузке пакета)
    from .prompts import RISK_FACTORS
    route_label, article = ROUTES[route]
    return {
        "risk_level": risk,
        "risk_factors": [RISK_FACTORS[f] for f in factors],
        "check_route": route,
        "legal_qualification": route_label + (f" ({article})" if risk == 1 and article else ""),
        "legal_basis": [], "court_basis": [],
        "counterparty_category": category,
        "decision_argumentation": argument + " " + NOTE,
        "risk_explanation": explanation,
        "challenge_criteria": "",
        "recommendation": recommendation,
        "connections_basis": "Связи сторон не анализировались (режим без GigaChat).",
        "documents_to_request": docs,
        "verification_goal": "",
        "used_tools": [],
    }


def rule_based_analysis(record: dict, big_amount: float) -> dict:
    """Одна операция -> результат в формате finalize_operation."""
    op_type = str(record.get("operation_type") or "")
    direction = record.get("direction")
    period = record.get("period")
    amount = abs(float(record.get("amount") or 0))
    docs = [d for d in (record.get("requested_documents") or []) if isinstance(d, str)]
    unknown = op_type.startswith("Неопознанный") or not op_type

    if direction == "убытие" and op_type in DEBT_TYPES and period in RECENT_PERIODS | {"6–12 месяцев до заявления"}:
        recent = period in RECENT_PERIODS
        if recent or amount >= big_amount:
            return _result(
                1, "preference", ["debt_repayment"],
                "Платёж погашает ранее возникшее обязательство в период, близкий к заявлению о банкротстве; график платежей и "
                "наличие иных кредиторов в данных не переданы.",
                "Решающий фактор — погашение долга в чувствительный период; оценка изменится, если платёж шёл по графику.",
                "Приоритетная ручная проверка очерёдности расчётов. Запросить: " + "; ".join(docs[:4]) + ".",
                docs, "Кредитор по долговому обязательству")
    if direction == "убытие" and op_type in NO_COUNTER_PERFORMANCE_TYPES and period in LONG_PERIODS:
        factors = ["no_counter_performance"] + (["cash_out"] if op_type in {"Снятие наличных", "Перевод на банковскую карту"} else [])
        return _result(
            1, "asset_withdrawal", factors,
            "Выбытие средств, по смыслу назначения не предполагающее встречного предоставления.",
            "Решающий фактор — отсутствие встречного предоставления по смыслу операции.",
            "Приоритетная ручная проверка деловой цели и влияния на имущественную массу. Запросить: " + "; ".join(docs[:4]) + ".",
            docs, "не установлено по доступным данным")
    if direction == "убытие" and unknown and amount >= big_amount and period in RECENT_PERIODS:
        return _result(
            1, "counter_performance", ["unclear_outflow"],
            "Крупное списание в чувствительный период с нераскрытым основанием и типом операции.",
            "Смысл списания и встречное исполнение не установлены по доступным данным.",
            "Приоритетная ручная проверка основания платежа. Запросить: " + "; ".join(docs[:4]) + ".",
            docs, "Контрагент-юрлицо с неустановленной ролью")
    if op_type in DOC_CHECK_TYPES and direction == "убытие":
        return _result(
            0, "documents", [],
            "Платёж похож на обычную оплату по хозяйственному договору; предмет понятен.",
            "Обычная версия не опровергнута, нехватка документов — повод запросить их, а не основание для проверки.",
            "Документальная проверка основания. Запросить: " + "; ".join(docs[:4]) + ".",
            docs[:4], "не установлено по доступным данным")
    route = "none"
    return _result(
        0, route, [],
        "Признаков материального вопроса по доступным данным операции не выявлено." if not unknown
        else "Назначение платежа не позволяет определить тип операции; материального вопроса по данным не выявлено.",
        "Типовая или не вызывающая вопросов операция.",
        "Дополнительные действия не требуются." if not unknown else "Дополнительные действия не требуются; при необходимости запросить расшифровку назначения у банка.",
        [], "не установлено по доступным данным")


def big_amount_threshold(df: pd.DataFrame) -> float:
    amounts = pd.to_numeric(df["amount"], errors="coerce").abs().dropna()
    return float(amounts.quantile(0.90)) if len(amounts) else float("inf")
