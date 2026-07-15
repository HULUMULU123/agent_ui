from __future__ import annotations

import os

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

# Входной контракт из тетрадки BankOperation.
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
    "debit_name",
    "debit_inn",
    "credit_name",
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
    # Предварительный тип операции от отдельного классификатора (см.
    # operation_classifier.py), уже прошедший собственную проверку/коррекцию.
    # Это подсказка для transaction_category, а не готовое решение -- см. ANALYZER_PROMPT.
    "operation_type",
]

# Выходной контракт из AnalyzerClusterResult.operations.
AGENT_OUTPUT_FIELDS = [
    "cluster_id",
    "idx",
    "transaction_category",
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

# Путь к таблице обогащения контрагентов по ИНН (перенесено из
# agent_v3_adaptive_clusters_1.ipynb, где она называлась spark_data/spark_inn_data.xlsx —
# статический экспорт из DWH/Spark-джобы, а не живое подключение к Spark).
# Если переменная не задана или файл не найден, обогащение молча пропускается —
# так же, как в тетрадке.
SPARK_ENRICHMENT_PATH = os.getenv("SPARK_ENRICHMENT_PATH", "")

COURT_FILING_DATE_DEFAULT = os.getenv("COURT_FILING_DATE", "2025-09-11")
MIN_ABS_AMOUNT_FOR_LLM = float(os.getenv("MIN_ABS_AMOUNT_FOR_LLM", "10000"))
RANDOM_STATE = int(os.getenv("RANDOM_STATE", "42"))

# --- Адаптивная стратегия анализа кластеров (перенесено из agent_v3_adaptive_clusters_1.ipynb) ---

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

# Порог однородности кластера. None -> используется PROPAGATION_CONFIDENCE_LOW.
CLUSTER_HOMOGENEITY_THRESHOLD = os.getenv("CLUSTER_HOMOGENEITY_THRESHOLD")
CLUSTER_HOMOGENEITY_THRESHOLD = float(CLUSTER_HOMOGENEITY_THRESHOLD) if CLUSTER_HOMOGENEITY_THRESHOLD else None
MAX_ISOLATED_SHARE = float(os.getenv("MAX_ISOLATED_SHARE", "0.30"))
MAX_REPRESENTATIVES_PER_CLUSTER = int(os.getenv("MAX_REPRESENTATIVES_PER_CLUSTER", "8"))
MAX_OPERATIONS_PER_LLM_BATCH = int(os.getenv("MAX_OPERATIONS_PER_LLM_BATCH", "20"))

# Сколько поисковых запросов каждого типа реально выполняется за один вызов
# search_practice_and_normative (нормативка / судебная практика).
MAX_QUERIES_PER_TYPE = int(os.getenv("MAX_QUERIES_PER_TYPE", "2"))
