# Gradio Finance Dashboard

Интерфейс финансового AI-агента на Gradio с кастомным HTML/CSS/JS.

## Запуск

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
python -m pip install -r requirements.txt
python app.py
```

Windows:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip setuptools wheel
python -m pip install -r requirements.txt
python app.py
```

## Один файл данных

Все данные подтягиваются из одного JSON-файла:

```text
assets/dashboard_payload.json
```

`app.py` читает его при запуске и передает в браузер через `window.__FINANCE_DASHBOARD_DATA__`.

Можно указать другой файл через переменную окружения:

```bash
DASHBOARD_DATA_PATH=/absolute/path/to/dashboard_payload.json python app.py
```

Если агент работает в Jupyter, самый простой сценарий такой:

```python
from dashboard_payload import build_dashboard_payload, save_dashboard_payload

payload = build_dashboard_payload(
    transactions=transactions_df,          # pandas DataFrame или list[dict]
    documents=documents_df,                # необязательно
    charts=charts_dict,                    # dict с данными графиков
    signals=signals_df,                    # необязательно
    legal_report=legal_report_df,          # необязательно
    counterparty_registry=registry_df,     # необязательно
    modal=modal_dict,                      # необязательно
)

save_dashboard_payload(payload)            # пишет assets/dashboard_payload.json
```

После записи JSON перезапусти `python app.py`, чтобы приложение перечитало данные.

## Контракт данных

Минимальная структура:

```python
charts_dict = {
    "cashFlowMonthly": [
        {"month": "Июль", "incoming": 12.2, "outgoing": 9.1},
    ],
    "expenseCategories": [
        {"category": "Подрядчики", "value": 4.8},
    ],
    "topCounterparties": [
        {"name": "ООО Астра", "value": 8.6},
    ],
    "riskDistribution": [
        {"level": "Низкий", "value": 64},
    ],
    "newCounterparties": [
        {"week": "Нед 1", "newPartners": 4, "checkPartners": 1},
    ],
    "dailyAmountBuckets": [
        {"bucket": "0–10K", "value": 1},
    ],
    "dailyActivity": [
        {"day": "Пн", "incoming": 10, "outgoing": 8},
    ],
}
```

Транзакции должны иметь поля:

```python
{
    "date": "11.11.2024",
    "time": "09:10",
    "doc": "DOC-12010",
    "type": "Поступление",
    "category": "Налоги",
    "counterparty": "ООО Астра",
    "inn": "774500170",
    "kpp": "775500110",
    "amount": 2100000,
}
```

## Что реализовано

- Таблицы с сортировкой, фильтрацией, пагинацией и CSV-экспортом.
- Таблица транзакций растягивается на весь блок, но сохраняет фиксированную ширину колонок.
- JSON-backed SVG-графики: line, area, bar, donut, cashflow.
- Более плавный cashflow-график через clip-path reveal вместо дерганого stroke-dash.
- Анимация появления блоков через IntersectionObserver.
- Отложенная анимация графиков после появления карточки на экране.
- Count-up анимация числовых метрик.
- Рабочий progressbar этапов анализа.
- Модальное окно с закрытием по кнопке, фону и Escape.

## Запуск анализа из интерфейса

В сборке добавлен backend bridge между кастомной HTML-карточкой загрузки и Python-кодом Gradio.

Сценарий:

1. Нажмите `Выбрать файл` в блоке загрузки документа.
2. Выберите `.xlsx`, `.xls` или `.csv`.
3. Нажмите `Запустить анализ`.
4. Откроется модальное окно загрузки агента.
5. Gradio вызовет Python callback `start_agent_analysis()`.
6. Callback передаст путь к файлу в `agent_runner.py`.
7. Тестовая заглушка прочитает таблицу, прогонит `tqdm` и выведет `df.head()` в консоль.

Главный файл для связи с Jupyter/агентом:

```text
agent_runner.py
```

Сейчас в нем находится заглушка:

```python
from agent_runner import run_uploaded_statement_stub

result = run_uploaded_statement_stub("statement.xlsx")
```

Правильная схема для реального агента: не запускать `.ipynb` напрямую из Gradio, а вынести код агента из ноутбука в обычный `.py`-модуль. Тогда и ноутбук, и Gradio будут импортировать одну и ту же функцию:

```python
from agent_runner import run_agent

result = run_agent(file_path)
```

Временный вариант через notebook возможен через `papermill` или `nbconvert`, но это хуже для демо: сложнее отлаживать, тяжелее ловить ошибки, медленнее стартует, хуже контролируется прогресс.
