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
]

COURT_FILING_DATE_DEFAULT = os.getenv("COURT_FILING_DATE", "2025-09-11")
MIN_ABS_AMOUNT_FOR_LLM = float(os.getenv("MIN_ABS_AMOUNT_FOR_LLM", "10000"))
SAMPLE_PER_CLUSTER = int(os.getenv("SAMPLE_PER_CLUSTER", "5"))
RANDOM_STATE = int(os.getenv("RANDOM_STATE", "42"))
