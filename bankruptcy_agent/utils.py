from __future__ import annotations

import json
import math
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
def int_safe(value: Any, default: int = 0) -> int:
    try:
        if pd.isna(value):
            return default
        return int(float(value))
    except Exception:
        match = re.search(r"\d+", str(value))
        return int(match.group(0)) if match else default
def to_float(value: Any, default: float = 0.0) -> float:
    try:
        if pd.isna(value):
            return default
        if isinstance(value, str):
            value = value.replace(" ", "").replace("\u00a0", "").replace(",", ".")
        return float(value)
    except Exception:
        return default
def format_date(value: Any) -> str:
    text = str(value).strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}", text):
        dt = pd.to_datetime(value, errors="coerce")
    else:
        dt = pd.to_datetime(value, errors="coerce", dayfirst=True)
    if pd.isna(dt):
        return ""
    return dt.strftime("%d.%m.%Y")
def format_money(value: Any) -> str:
    amount = to_float(value, 0.0)
    sign = "-" if amount < 0 else ""
    amount = abs(amount)
    return f"{sign}{amount:,.0f} ₽".replace(",", " ")
def short_string(value: Any, limit: int) -> str:
    text = clean_text(value)
    return text if len(text) <= limit else text[: max(0, limit - 1)] + "…"
def clean_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(_jsonable(value), ensure_ascii=False)
    return str(value).strip()
def parse_maybe_json(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if value is None:
        return {}
    if isinstance(value, float) and math.isnan(value):
        return {}
    text = str(value).strip()
    if not text:
        return {}
    if (text.startswith("{") and text.endswith("}")) or (text.startswith("[") and text.endswith("]")):
        try:
            return json.loads(text)
        except Exception:
            return text
    return text
def ensure_list(value: Any) -> list[str]:
    parsed = parse_maybe_json(value)
    if parsed is None or parsed == {}:
        return []
    if isinstance(parsed, list):
        return [clean_text(v) for v in parsed if clean_text(v)]
    if isinstance(parsed, dict):
        return [clean_text(v) for v in parsed.values() if clean_text(v)]
    text = clean_text(parsed)
    if not text:
        return []
    if ";" in text:
        return [part.strip() for part in text.split(";") if part.strip()]
    return [text]
def unique_strings(values: list[Any]) -> list[str]:
    seen = set()
    out = []
    for value in values:
        text = clean_text(value)
        key = text.lower()
        if text and key not in seen:
            seen.add(key)
            out.append(text)
    return out
def save_payload(payload: dict[str, Any], output_path: str | Path) -> Path:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_jsonable(payload), ensure_ascii=False, indent=2), encoding="utf-8")
    return path
def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    if isinstance(value, (pd.Timestamp, datetime, date)):
        return value.isoformat()
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return float(value)
    try:
        if pd.isna(value):
            return None
    except Exception:
        pass
    return value
