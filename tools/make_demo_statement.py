"""Учебная выписка для демонстрации приложения (выдуманные организации, ИНН с корректной контрольной суммой).

    python tools/make_demo_statement.py            # -> examples/demo_statement.xlsx
"""
from __future__ import annotations

import random
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

OUT = Path(__file__).resolve().parent.parent / "examples" / "demo_statement.xlsx"
OWNER = ("7712345675", "ООО «Торговый Дом Вектор»")


def inn10(seed: int) -> str:
    rnd = random.Random(seed)
    digits = [rnd.randint(1, 9)] + [rnd.randint(0, 9) for _ in range(8)]
    weights = [2, 4, 10, 3, 5, 9, 4, 6, 8]
    check = sum(d * w for d, w in zip(digits, weights)) % 11 % 10
    return "".join(map(str, digits + [check]))


PARTIES = {
    "supplier1": (inn10(1), "ООО «СтройМатериалы Плюс»"), "supplier2": (inn10(2), "ООО «ТрансЛогистик»"),
    "supplier3": (inn10(3), "АО «Промснаб»"), "buyer1": (inn10(4), "ООО «Альфа Трейд»"), "buyer2": (inn10(5), "АО «СтройИнвест»"),
    "buyer3": (inn10(6), "ООО «Гарант Плюс»"), "bank": (inn10(7), "ПАО «Городской банк»"), "fns": ("7727406020", "УФК по г. Москве (ИФНС № 27)"),
    "related": (inn10(8), "ООО «Консалт Партнёр»"), "lender": (inn10(9), "ООО «Финанс Групп»"), "landlord": (inn10(10), "ООО «Бизнес-Центр Арбат»"),
    "telecom": (inn10(11), "ПАО «Связь Телеком»"), "energy": (inn10(12), "ПАО «Энергосбыт»"),
}


def build(seed: int = 7) -> pd.DataFrame:
    rnd = random.Random(seed)
    rows: list[dict] = []

    def add(d: date, party: str, amount: float, purpose: str) -> None:
        """amount < 0 — списание со счёта владельца, > 0 — поступление."""
        inn, name = PARTIES[party]
        owner_inn, owner_name = OWNER
        if amount < 0:
            rows.append({"date": d, "debit_inn": owner_inn, "debit_name": owner_name, "credit_inn": inn, "credit_name": name, "amount": round(amount, 2), "purpose": purpose})
        else:
            rows.append({"date": d, "debit_inn": inn, "debit_name": name, "credit_inn": owner_inn, "credit_name": owner_name, "amount": round(amount, 2), "purpose": purpose})

    month = date(2023, 1, 1)
    n = 0
    while month < date(2025, 10, 1):
        def day(k: int) -> date:
            return month + timedelta(days=min(k, 27))
        add(day(4), "fns", -rnd.uniform(380_000, 520_000), f"Уплата НДФЛ за {month:%m.%Y}")
        add(day(14), "fns", -rnd.uniform(250_000, 340_000), f"Страховые взносы за {month:%m.%Y}")
        add(day(9), "landlord", -240_000, f"Арендная плата за {month:%m.%Y} по договору аренды № 14/2022 от 01.03.2022, НДС 20%")
        add(day(11), "energy", -rnd.uniform(45_000, 90_000), f"Оплата электроэнергии за {month:%m.%Y}, счёт № {rnd.randint(1000, 9999)}")
        add(day(12), "telecom", -rnd.uniform(12_000, 25_000), f"Оплата услуг связи за {month:%m.%Y}")
        add(day(2), "bank", -rnd.uniform(4_000, 9_000), "Комиссия за расчётно-кассовое обслуживание")
        add(day(26), "bank", -rnd.uniform(1_200_000, 1_800_000), f"Заработная плата за {month:%m.%Y}, реестр № {rnd.randint(10, 99)}")
        for _ in range(rnd.randint(3, 5)):
            supplier = rnd.choice(["supplier1", "supplier2", "supplier3"])
            add(day(rnd.randint(1, 27)), supplier, -rnd.uniform(150_000, 2_800_000),
                rnd.choice([f"Оплата по договору поставки № {rnd.randint(10, 400)} от {rnd.randint(1, 28):02d}.0{rnd.randint(1, 9)}.2023 за товар",
                            f"Оплата за строительные материалы по счёту № {rnd.randint(100, 999)}", "Оплата транспортных услуг по акту",
                            "Оплата по договору № 55"]))
        for _ in range(rnd.randint(4, 7)):
            buyer = rnd.choice(["buyer1", "buyer2", "buyer3"])
            add(day(rnd.randint(1, 27)), buyer, rnd.uniform(400_000, 4_500_000),
                rnd.choice([f"Оплата за товар по счёту № {rnd.randint(100, 999)}", "Оплата по договору поставки", f"Предоплата по договору № {rnd.randint(1, 90)}",
                            "Оплата за продукцию, в т.ч. НДС 20%"]))
        month = (month + timedelta(days=32)).replace(day=1)
        n += 1

    # события, ради которых и нужен анализ (период подозрительности — последние 3 года до 10.10.2025)
    add(date(2025, 3, 17), "bank", -2_400_000, "Досрочное погашение кредита по кредитному договору № 17-К от 12.05.2022")
    add(date(2025, 7, 21), "bank", -3_100_000, "Погашение основного долга по кредитному договору № 17-К")
    add(date(2025, 9, 12), "related", -4_800_000, "Оплата по договору консультационных услуг № 137 от 01.09.2025")
    add(date(2025, 8, 4), "related", -3_200_000, "Оплата по договору консультационных услуг № 125")
    add(date(2025, 6, 30), "related", -2_750_000, "Оплата консультационных услуг по договору № 98")
    add(date(2025, 9, 25), "lender", -1_950_000, "Возврат займа по договору займа № 3 от 10.01.2024")
    add(date(2025, 5, 14), "lender", -1_200_000, "Возврат займа по договору займа № 3, проценты за период")
    add(date(2025, 8, 18), "lender", 5_000_000, "Предоставление займа по договору займа № 7")
    add(date(2025, 4, 9), "related", -900_000, "Безвозмездная финансовая помощь")
    add(date(2025, 2, 11), "bank", -650_000, "Снятие наличных на хозяйственные нужды")
    add(date(2025, 9, 3), "bank", -480_000, "Перевод на личные нужды")
    add(date(2025, 6, 5), "supplier3", -1_350_000, "Оплата по счёту 4471")
    add(date(2025, 9, 29), "supplier1", -620_000, "Платёж по письму б/н")
    add(date(2024, 12, 19), "bank", -250_000, "Перечисление")
    df = pd.DataFrame(rows).sort_values("date").reset_index(drop=True)
    df["date"] = pd.to_datetime(df["date"])
    return df


if __name__ == "__main__":
    OUT.parent.mkdir(parents=True, exist_ok=True)
    frame = build()
    frame.to_excel(OUT, index=False)
    print(OUT, len(frame), "операций;", frame["date"].min().date(), "—", frame["date"].max().date())
