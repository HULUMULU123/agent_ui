# Gradio dashboard для агента банкротного риска

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

## Как работает связка с агентом

UI вызывает `agent_runner.run_uploaded_statement()`.

Сценарий в интерфейсе:

1. Загрузить Excel/CSV на главной странице.
2. Включить или выключить тестовый режим.
3. Нажать `Запустить анализ`.
4. Backend читает файл, формирует payload и обновляет таблицы/графики.

## Тестовый режим

Если переключатель тестового режима включен, реальный агент не запускается. Интерфейс показывает заранее подготовленный mock payload из:

```text
assets/dashboard_payload.json
```

При этом загруженный файл всё равно читается, а `df.head()` выводится в консоль для проверки загрузки.

## Рабочий режим

Если тестовый режим выключен, выполняется pipeline из:

```text
bankruptcy_agent_adapter.py
```

Он приводит входной файл к контракту тетрадки и формирует dashboard payload.

Если рядом есть файл:

```text
real_agent_entrypoint.py
```

и в нем есть функция:

```python
def run_real_agent(transactions):
    ...
```

то adapter попытается вызвать настоящий агент. Функция может вернуть:

- `pandas.DataFrame`;
- `list[dict]` с операциями;
- `dict` с ключом `operations`;
- `dict` с `analyzer_result.operations`;
- список `cluster_results` из тетрадки с `parsed_analysis`.

Если настоящий агент недоступен, используется deterministic fallback в той же структуре, чтобы интерфейс не падал.

## Входные поля из тетрадки

Основной контракт входа:

```text
idx, cluster, date, interval, direction, purpose, amount,
anomaly_score, anomaly_model_score,
debit_name, debit_inn, credit_name, credit_inn,
credit_registration_date, credit_status, credit_okved,
credit_num_court_cases, credit_sum_court_cases,
credit_loss_profit_amount, credit_tax_arrears,
collect_all_graph_connections.description,
court_filing_date
```

Если части колонок нет, adapter создает безопасные значения по умолчанию и выводит предупреждения в summary.

## Выходные поля агента

Основной контракт выхода из анализатора:

```text
idx, transaction_category, amount, counterparty_category,
connections_basis, challenge_criteria, risk_level,
legal_qualification, legal_basis, court_basis,
decision_argumentation, risk_explanation,
recommendation, used_tools
```

Поддерживаются совместимые варианты:

```text
connection_basis       -> connections_basis
legal_qulification     -> legal_qualification
cluster                -> cluster_id
counterparty_inn       -> inn
```

## Откуда берутся сигналы и аномалии в UI

Сигналы не строятся напрямую из `anomaly_score`.

Они извлекаются из результата агента:

```text
connections_basis / connection_basis
legal_qualification
challenge_criteria
recommendation.documents_to_request
```

В блоке `Сигналы из связей и квалификации` считаются:

- сильная связь сторон;
- средняя связь сторон;
- маршрут 61.3;
- маршрут 61.2;
- документы к запросу;
- высокая готовность к оспариванию;
- предупреждения по входному файлу.

## Единый файл данных

Основной payload для UI:

```text
assets/dashboard_payload.json
```

Runtime payload после запуска анализа:

```text
assets/dashboard_payload_runtime.json
```

Можно передать внешний payload:

```bash
DASHBOARD_DATA_PATH=/absolute/path/to/dashboard_payload.json python app.py
```

## Файлы проекта

```text
app.py                         # Gradio-приложение
agent_runner.py                # backend entrypoint для кнопки запуска
bankruptcy_agent_adapter.py    # адаптер тетрадки агента к dashboard payload
dashboard_payload.py           # helper для сохранения payload из Jupyter
assets/dashboard.html          # HTML интерфейса
assets/dashboard.js            # таблицы, графики, загрузка, bridge с Gradio
assets/styles.css              # стили, без визуальных анимаций
assets/dashboard_payload.json  # mock payload для тестового режима
```

## Fixed13 behavior

- Главная страница больше не показывает KPI, графики и таблицы неизвестной выписки до запуска анализа.
- После загрузки файла и нажатия `Запустить анализ` на главной странице появляется шапка загруженной таблицы.
- Вся статистика по входной выписке и результатам агента отображается на странице `Статистика` только после завершения анализа.
- Таблица транзакций и графики перенесены в аналитический раздел статистики.
- Сетка карточек выровнена: карточки в одной строке имеют одинаковую высоту, таблицы используют фиксированную раскладку колонок.
- Визуальные анимации отключены для стабильной работы на слабых устройствах. Элементы интерфейса не удалены.


## Fixed v4: тестовый режим и место для LLM/embeddings

Тестовый режим больше не требует загрузки файла. Если на главной странице включен переключатель
`Тестовый режим`, кнопка `Запустить анализ` нажимает отдельную backend-кнопку `agent-start-test-button`.
Backend использует `assets/mock_statement.xlsx`, читает его шапку и показывает mock payload.

Рабочий режим требует Excel/CSV. Если тестовый режим выключен и файл не выбран, backend не запускается.

Место для настройки реального агента находится в файле:

```text
real_agent_entrypoint.py
```

Ищи блок:

```python
# МЕСТО ДЛЯ НАСТРОЙКИ LLM И EMBEDDING MODEL
llm = None
embedding_model = None
```

Туда нужно перенести определение LLM, embedding model, retriever/vectorstore, tools и сборку LangGraph/app.
Единственная функция, которую вызывает Gradio adapter:

```python
def run_real_agent(transactions: pd.DataFrame):
    ...
```

Пока эта функция не реализована, приложение не падает: `bankruptcy_agent_adapter.py` перехватывает ошибку
и использует fallback в том же формате данных.

## Fixed14: тестовый режим и место настройки моделей

### Тестовый режим

Тестовый режим теперь запускается отдельной backend-кнопкой `agent-start-test-button`, а рабочий режим — кнопкой `agent-start-button`. Это сделано намеренно: скрытый `gr.Checkbox` нестабилен при управлении из кастомного HTML/JS, поэтому состояние тестового режима больше не прокидывается через checkbox Gradio.

Тестовый режим можно запускать даже без пользовательского файла. В этом случае для шапки таблицы используется встроенная тестовая выписка:

```text
assets/mock_statement.xlsx
```

Реальный агент в тестовом режиме не вызывается, LLM и embeddings не нужны.

### Где указать LLM и embedding-модель

Место для настройки вынесено в отдельный файл:

```text
real_agent_entrypoint.py
```

Ищи блок:

```python
# USER MODEL CONFIG — ЗАПОЛНИТЬ САМОСТОЯТЕЛЬНО
llm = None
llm_helper = None
embeddings = None
```

Туда нужно подставить твои модели. Ниже в этом же файле есть блок:

```python
# TODO_USER_AGENT_PIPELINE
```

Туда переносится фактический запуск графа/цепочек из Jupyter-тетрадки. Пока этот блок не заполнен, рабочий режим не падает: `bankruptcy_agent_adapter.py` перехватывает ошибку и использует fallback-результат в том же формате для проверки интерфейса.

## Обновление v5

- Шапка загруженной выписки на главной странице теперь рендерится как HTML-таблица, а не как моноширинный текстовый блок.
- Backend передает preview в JSON-формате `{columns, rows}`; в консоль по-прежнему печатается обычный `df.head().to_string()`.
- На странице «Статистика» добавлены финальные CSS-override правила для стабильной сетки: одинаковые высоты карточек в строках, единый gap между секциями, фиксированное поведение chart-card/table-card/signals-card.


## Архитектура агента

Агентная часть вынесена в пакет `bankruptcy_agent/`:

- `config.py` — входной/выходной контракт, обязательные поля, параметры sampling.
- `schemas.py` — dataclass-структуры результата backend pipeline.
- `models.py` — единственное место для определения `llm`, `llm_helper`, `embeddings` и внешних tools.
- `io.py` — чтение Excel/CSV.
- `preprocessing.py` — нормализация колонок, расчет interval, idx, fallback-кластеризация, LLM-выборка.
- `real_agent_bridge.py` — безопасный вызов реального агента из `real_agent_entrypoint.py`.
- `fallback.py` — deterministic fallback без LLM для стабильного демо.
- `output_normalizer.py` — приведение output агента к единому контракту dashboard.
- `extraction.py` — извлечение `connections_basis`, `legal_qualification`, `challenge_criteria`, документов и сигналов.
- `dashboard.py` — сбор KPI, таблиц, графиков и JSON payload для интерфейса.
- `pipeline.py` — единый orchestration entrypoint: файл → подготовка → агент → payload.

Для подключения моделей редактируйте только `bankruptcy_agent/models.py`.
Для переноса реального графа из ноутбука редактируйте `real_agent_entrypoint.py`.
Старый `bankruptcy_agent_adapter.py` оставлен как compatibility-wrapper, чтобы старые импорты не ломались.


## Реальный агент из тетрадки

Агентный pipeline из `agent_v2_r_professional_clean.ipynb` вынесен в пакет `bankruptcy_agent`.
Запуск из UI идет так:

```text
Gradio upload -> agent_runner.py -> bankruptcy_agent/pipeline.py -> real_agent_entrypoint.py -> bankruptcy_agent/notebook_agent.py
```

В `bankruptcy_agent/notebook_agent.py` реализован цикл из тетрадки:

```text
cluster -> orchestrator -> tools -> orchestrator -> analyzer -> DataFrame результата
```

Настройка моделей находится в `bankruptcy_agent/models.py`:

```python
llm = ...
llm_helper = ...
embeddings = ...
retriever = ...
practice_tool = ...
risk_db = ...
```

Минимально нужен `llm`. Если оркестратор запросит нормативку/практику, нужны также `llm_helper`, `retriever`, `practice_tool`. Если оркестратор запросит историю риска, нужен `risk_db`.

По умолчанию, если реальный агент не настроен или падает, UI использует fallback, чтобы демо не ломалось. Для жесткой проверки поставьте:

```bash
STRICT_REAL_AGENT=1 python app.py
```

Тогда ошибка реального агента не будет скрываться fallback-режимом.

## Полный режим агента: что теперь уже готово

В `bankruptcy_agent/models.py` больше не нужно вручную создавать `risk_db` и `practice_tool`.

Готово из тетрадки:

- `risk_db = RiskMemoryDB("risk_memory.db")` создается автоматически;
- `practice_tool = CourtPracticeSearchTool(...)` создается автоматически, если рядом есть папка `court_practice_storage` с файлами:
  - `court_practice.sqlite`
  - `court_practice.faiss`
  - `faiss_metadata.pkl`
- `retriever` создается автоматически через `build_normative_retriever(...)`, если задан `embeddings`.

Ручной блок теперь только один:

```python
llm = ...
llm_helper = ...
embeddings = ...
```

Если используешь FAISS-индексы нормативки и судебной практики, поставь дополнительные зависимости:

```bash
python -m pip install -r requirements-agent.txt
```

Файлы индексов должны лежать рядом с `app.py`:

```text
faiss_normative_db/
  index.faiss
  index.pkl

court_practice_storage/
  court_practice.sqlite
  court_practice.faiss
  faiss_metadata.pkl
```

`retriever` — единственное, чего не было как готового runtime-объекта в тетрадке. Теперь он тоже автособирается, если задан `embeddings`; если нормативного индекса нет, будет создан dummy-индекс, как в notebook.
