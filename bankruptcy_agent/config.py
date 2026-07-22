from __future__ import annotations

import os
from pathlib import Path

# =============================================================================
# CONFIG -- единый словарь настроек, как в agent_v3_adaptive_clusters_1.ipynb.
# Ниже, для обратной совместимости с существующими импортами по всему проекту,
# из CONFIG производятся отдельные именованные константы -- сам CONFIG остаётся
# источником истины и тем местом, которое стоит редактировать в первую очередь.
# =============================================================================

ROOT = Path(__file__).resolve().parent.parent

CONFIG: dict = {
    # --- Банкротный контекст ---
    "court_filing_date": os.getenv("COURT_FILING_DATE", "2025-09-11"),
    "min_abs_amount_for_llm": float(os.getenv("MIN_ABS_AMOUNT_FOR_LLM", "10000")),
    "random_state": int(os.getenv("RANDOM_STATE", "42")),

    # --- Обогащение по ИНН (перенесено из тетрадки: spark_data/spark_inn_data.xlsx --
    # статический экспорт из DWH/Spark-джобы, а не живое подключение к Spark) ---
    "spark_enrichment_path": os.getenv("SPARK_ENRICHMENT_PATH", ""),

    # --- Адаптивная стратегия анализа кластеров ---
    # Порог однородности кластера. None -> используется порог уверенности
    # распространения (propagation_confidence_low): если средняя пара операций
    # кластера ниже него, перенос объяснений внутри кластера ненадежен.
    "cluster_homogeneity_threshold": (
        float(os.getenv("CLUSTER_HOMOGENEITY_THRESHOLD")) if os.getenv("CLUSTER_HOMOGENEITY_THRESHOLD") else None
    ),
    # Доля "изолированных" операций (нет достаточно похожего соседа),
    # при превышении которой кластер считается разреженным.
    "max_isolated_share": float(os.getenv("MAX_ISOLATED_SHARE", "0.30")),
    # Верхняя граница числа представителей компактного кластера (страховка
    # стоимости; фактический размер выборки определяется покрытием).
    "max_representatives_per_cluster": int(os.getenv("MAX_REPRESENTATIVES_PER_CLUSTER", "8")),
    # Максимум операций в одном обращении к агенту (нарезка разреженных кластеров).
    # Это НЕ ограничение самой модели -- это размер ЗАДАЧИ, которую отдают модели за
    # один вызов: анализатор обязан вернуть JSON сразу по ВСЕМ операциям батча (11
    # полей на операцию, 2 вложенных объекта). Вторичный рычаг против обрезки ответа:
    # основная причина обрезки -- объём tool_results (см. legal_fragment_max_chars
    # ниже), но на больших батчах объём ответа складывается с объёмом tool_results.
    "max_operations_per_llm_batch": int(os.getenv("MAX_OPERATIONS_PER_LLM_BATCH", "10")),
    # Анализировать ли разреженные кластеры агентом. False -> разреженные кластеры
    # полностью исключаются из LLM-анализа (операции помечаются на ручную проверку),
    # что снижает стоимость прогона на шумных данных.
    "analyze_sparse_clusters": os.getenv("ANALYZE_SPARSE_CLUSTERS", "true").strip().lower() not in ("false", "0", "no"),

    # --- Классификатор типа операции ---
    # Сколько операций сэмплируется из каждого кластера для классификации (тип
    # затем распространяется на весь кластер по сходству purpose).
    "classifier_sample_per_cluster": int(os.getenv("CLASSIFIER_SAMPLE_PER_CLUSTER", "20")),
    # Минимально приемлемая уверенность классификатора: типы с уверенностью ниже
    # уходят в блок перепроверки (переклассифицируются индивидуально).
    # Порядок: low < medium < high.
    "classifier_confidence_min": os.getenv("CLASSIFIER_CONFIDENCE_MIN", "medium"),
    # Минимальное сходство при распространении типа по кластеру: если ближайшая
    # классифицированная операция похожа слабее -- тип считается неуверенным и
    # операция уходит в перепроверку, а не наследует чужой тип вслепую.
    "classifier_propagation_similarity_min": float(os.getenv("CLASSIFIER_PROPAGATION_SIMILARITY_MIN", "0.55")),
    # Включить блок перепроверки неуверенных типов (индивидуальная переклассификация).
    "classifier_recheck": os.getenv("CLASSIFIER_RECHECK", "true").strip().lower() not in ("false", "0", "no"),

    # --- Нормативный модуль (search_practice_and_normative) ---
    # Предел длины ОДНОГО фрагмента, который возвращают search_normative_base и
    # search_case_law_practice (символов). Эти инструменты возвращают полный текст
    # найденного документа/карточки практики без ограничения длины, а результат
    # накапливается в tool_results по ходу всего цикла оркестратора -- независимо
    # от числа операций в батче. Ограничение режет каждый фрагмент у источника,
    # чтобы длинный документ не раздувал промпт анализатора и не обрезал ответ.
    "legal_fragment_max_chars": int(os.getenv("LEGAL_FRAGMENT_MAX_CHARS", "1200")),
    # Сколько поисковых запросов КАЖДОГО типа реально выполнять. Меньше запросов
    # -> меньше материала в tool_results -> короче контекст анализатора и ниже
    # риск обрезки итогового JSON по лимиту токенов.
    "legal_case_law_queries": int(os.getenv("LEGAL_CASE_LAW_QUERIES", "1")),
    "legal_normative_queries": int(os.getenv("LEGAL_NORMATIVE_QUERIES", "2")),

    # --- Быстрый режим (демо) ---
    # Если включено и для загруженного файла уже есть подготовленный результат
    # анализа с таким же именем в fast_mode_dir -- вместо реального прогона агента
    # (после искусственной задержки fast_mode_delay_seconds) отдаётся готовый payload.
    # Полезно для демонстраций на заранее подготовленных примерах без ожидания
    # реального времени работы LLM-конвейера.
    "fast_mode_enabled": os.getenv("FAST_MODE_ENABLED", "true").strip().lower() not in ("false", "0", "no"),
    "fast_mode_dir": os.getenv("FAST_MODE_DIR", str(ROOT / "assets" / "fast_mode_results")),
    "fast_mode_delay_seconds": float(os.getenv("FAST_MODE_DELAY_SECONDS", "6")),
}

# Сколько поисковых запросов каждого типа реально выполняется за один вызов
# search_practice_and_normative -- совместимость со старым единым лимитом
# (равен большему из двух новых, отдельных по нормативке/практике, лимитов).
CONFIG["max_queries_per_type"] = max(CONFIG["legal_case_law_queries"], CONFIG["legal_normative_queries"])


# =============================================================================
# Обратная совместимость: именованные константы, производные от CONFIG.
# Существующий код по всему проекту импортирует их по имени -- редактировать
# значения стоит через CONFIG выше, а не здесь.
# =============================================================================

COURT_FILING_DATE_DEFAULT = CONFIG["court_filing_date"]
MIN_ABS_AMOUNT_FOR_LLM = CONFIG["min_abs_amount_for_llm"]
RANDOM_STATE = CONFIG["random_state"]
SPARK_ENRICHMENT_PATH = CONFIG["spark_enrichment_path"]

CLUSTER_HOMOGENEITY_THRESHOLD = CONFIG["cluster_homogeneity_threshold"]
MAX_ISOLATED_SHARE = CONFIG["max_isolated_share"]
MAX_REPRESENTATIVES_PER_CLUSTER = CONFIG["max_representatives_per_cluster"]
MAX_OPERATIONS_PER_LLM_BATCH = CONFIG["max_operations_per_llm_batch"]
ANALYZE_SPARSE_CLUSTERS = CONFIG["analyze_sparse_clusters"]

CLASSIFIER_SAMPLE_PER_CLUSTER = CONFIG["classifier_sample_per_cluster"]
CLASSIFIER_CONFIDENCE_MIN = CONFIG["classifier_confidence_min"]
CLASSIFIER_PROPAGATION_SIMILARITY_MIN = CONFIG["classifier_propagation_similarity_min"]
CLASSIFIER_RECHECK = CONFIG["classifier_recheck"]

LEGAL_FRAGMENT_MAX_CHARS = CONFIG["legal_fragment_max_chars"]
LEGAL_CASE_LAW_QUERIES = CONFIG["legal_case_law_queries"]
LEGAL_NORMATIVE_QUERIES = CONFIG["legal_normative_queries"]
MAX_QUERIES_PER_TYPE = CONFIG["max_queries_per_type"]

FAST_MODE_ENABLED = CONFIG["fast_mode_enabled"]
FAST_MODE_DIR = Path(CONFIG["fast_mode_dir"])
FAST_MODE_DELAY_SECONDS = CONFIG["fast_mode_delay_seconds"]

# Веса гибридной метрики сходства операций: текст назначения + числовые
# признаки + контрагенты/графовые связи. Сумма весов = 1.0.
SIMILARITY_TEXT_WEIGHT = 0.45
SIMILARITY_NUMERIC_WEIGHT = 0.25
SIMILARITY_CONTEXT_WEIGHT = 0.30
SIMILARITY_NUMERIC_FEATURES = ["anomaly_score", "anomaly_model_score", "interval_days", "amount_log"]
GRAPH_CONNECTIONS_COLUMN = "collect_all_graph_connections.description"

# Пороги уверенности распространения объяснений: high — переносится
# автоматически; medium — переносится с пометкой; low — уходит на второй
# LLM-проход.
PROPAGATION_CONFIDENCE_HIGH = 0.85
PROPAGATION_CONFIDENCE_LOW = 0.65

REQUIRED_TRANSACTION_COLUMNS = {
    "date",
    "amount",
    "purpose",
    "cluster",
    "anomaly_score",
    "anomaly_model_score",
    "debit_inn",
    "credit_inn",
}

# Входной контракт из тетрадки BankOperation. debit_name/credit_name намеренно
# исключены -- реальные наименования сторон не передаются агенту (см.
# ANALYZER_PROMPT: "ИНН и наименования дебета/кредита тебе не передаются
# намеренно"); идентификация идёт по обезличенному ИНН (см. operation_classifier.py
# / notebook_agent.py: псевдонимы ORG_N).
AGENT_SOURCE_FIELDS = [
    "idx",
    "cluster",
    "date",
    "interval",
    "direction",
    "purpose",
    "amount",
    "anomaly_score",
    "anomaly_model_score",
    "debit_inn",
    "credit_inn",
    "credit_registration_date",
    "credit_status",
    "credit_okved",
    "credit_num_court_cases",
    "credit_sum_court_cases",
    "credit_loss_profit_amount",
    "credit_tax_arrears",
    "court_filing_date",
    "collect_all_graph_connections",
    "collect_all_graph_connections.description",
    # Тип операции от классификатора (см. operation_classifier.py): определён по
    # выборке кластера, распространён на весь кластер и при необходимости
    # индивидуально перепроверен. Анализатору запрещено его менять -- см.
    # ANALYZER_PROMPT, ШАГ 1a.
    "operation_type",
    "requested_documents",
]

# Выходной контракт из AnalyzerClusterResult.operations.
AGENT_OUTPUT_FIELDS = [
    "cluster_id",
    "idx",
    "operation_type",
    "amount",
    "counterparty_category",
    "connections_basis",
    "challenge_criteria",
    "risk_level",
    "legal_qualification",
    "legal_basis",
    "court_basis",
    "decision_argumentation",
    "risk_explanation",
    "recommendation",
    "used_tools",
    "status",
    "error",
    # Технические поля распространения/второго прохода (см. propagation.py).
    "analysis_source",
    "matched_representative_idx",
    "similarity_score",
    "propagation_confidence",
    "propagation_status",
    "propagation_note",
    "needs_review",
    "is_below_llm_threshold",
]
