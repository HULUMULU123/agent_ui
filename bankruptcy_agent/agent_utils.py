from __future__ import annotations

import json
import math
import re
from datetime import date, datetime
from typing import Any

import pandas as pd


def to_jsonable(value: Any) -> Any:
    """Преобразует pandas/numpy/даты в безопасные JSON-значения."""
    try:
        import numpy as np
        if isinstance(value, (np.integer,)):
            return int(value)
        if isinstance(value, (np.floating,)):
            if math.isnan(float(value)):
                return None
            return float(value)
    except Exception:
        pass
    if isinstance(value, (pd.Timestamp, datetime, date)):
        if pd.isna(value):
            return None
        return value.isoformat()
    try:
        if pd.isna(value):
            return None
    except Exception:
        pass
    if isinstance(value, dict):
        return {str(k): to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [to_jsonable(v) for v in value]
    return value


def records_for_llm(df: pd.DataFrame) -> list[dict[str, Any]]:
    records = []
    safe = df.copy()
    for row in safe.to_dict(orient="records"):
        records.append({str(k): to_jsonable(v) for k, v in row.items()})
    return records


def content_from_message(message: Any) -> str:
    if message is None:
        return ""
    if isinstance(message, str):
        return message
    if isinstance(message, dict):
        return json.dumps(message, ensure_ascii=False, default=str)
    return str(getattr(message, "content", message))


def extract_json_from_llm_response(message: Any) -> dict[str, Any]:
    """Извлекает JSON из ответа LLM по логике тетрадки, но с более жестким fallback."""
    if isinstance(message, dict):
        return message

    text = content_from_message(message).strip()
    if not text:
        return {}

    text = re.sub(r"^```json\s*", "", text.strip(), flags=re.IGNORECASE)
    text = re.sub(r"^```\s*", "", text.strip())
    text = re.sub(r"\s*```$", "", text.strip())
    text = text.strip()

    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {"_value": parsed}
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if match:
        try:
            parsed = json.loads(match.group(0))
            return parsed if isinstance(parsed, dict) else {"_value": parsed}
        except json.JSONDecodeError:
            pass

    return {"_parse_error": f"Не удалось распарсить JSON: {text[:500]}", "_raw_response": text[:4000]}


def normalize_to_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        if text.startswith("["):
            try:
                parsed = json.loads(text)
                if isinstance(parsed, list):
                    return parsed
            except Exception:
                pass
        return [text]
    return [value]


def safe_first(items: list[Any], default: str = "") -> str:
    if not items:
        return default
    item = items[0]
    return str(item) if item is not None else default
