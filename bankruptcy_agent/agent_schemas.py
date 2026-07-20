from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional, TypedDict


class BankOperation(TypedDict, total=False):
    idx: str
    cluster: Optional[int]
    date: Optional[str]
    interval: Optional[str]
    direction: Optional[Literal["прибытие", "убытие"]]
    purpose: Optional[str]
    amount: Optional[float]
    anomaly_score: Optional[float]
    anomaly_model_score: Optional[float]
    debit_name: Optional[str]
    debit_inn: Optional[str]
    credit_name: Optional[str]
    credit_inn: Optional[str]
    credit_registration_date: Optional[str]
    credit_status: Optional[str]
    credit_okved: Optional[str]
    credit_num_court_cases: Optional[int]
    credit_sum_court_cases: Optional[float]
    credit_loss_profit_amount: Optional[float]
    credit_tax_arrears: Optional[float]
    court_filing_date: Optional[str]
    collect_all_graph_connections: Optional[Dict[str, Any]]
    collect_all_graph_connections_description: Optional[str]
    operation_type: Optional[str]
    requested_documents: Optional[List[str]]


class ToolRequest(TypedDict, total=False):
    tool_call_id: str
    name: Literal["get_conterparty_risk", "search_practice_and_normative"]
    args: Dict[str, Any]
    reason: str
    hypothesis_codes: List[str]


class ToolExecutionResult(TypedDict, total=False):
    tool_call_id: Optional[str]
    name: str
    args: Dict[str, Any]
    success: bool
    result: Any
    error: Optional[str]
    limitations: List[str]


class AgentClusterResult(TypedDict, total=False):
    cluster_id: Any
    parsed_analysis: list[dict[str, Any]] | None
    raw_response: str | None
    status: str
    operations_count: int
