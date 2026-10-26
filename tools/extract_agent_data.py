"""Выгружает неизменяемые части ноутбука агента в модули finapp/agent/prompts.py и catalog.py.

    python tools/extract_agent_data.py /path/to/agent_v4_gc3_no_chains.ipynb

prompts.py  <- ячейка с промптами, маршрутами и кодами факторов риска;
catalog.py  <- каталог типов операций, матрица документов, ссылки на документы, промпты классификатора.
Логика (граф, кластеры, перенос, экспорт) живёт в остальных модулях пакета и пишется вручную.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "finapp" / "agent"

PROMPTS_MARK = "SHARED_AGENT_CONTRACT"
CATALOG_MARKS = ("CATALOG = {", "OPERATION_TYPE_BY_SUBJECT", "OPERATION_MATRIX = {", "DOC_REF_RE", "CLASSIFIER_PROMPT_TEMPLATE")


def main(path: Path) -> None:
    cells = ["".join(c["source"]) for c in json.loads(path.read_text(encoding="utf-8"))["cells"] if c["cell_type"] == "code"]
    prompts = next(c for c in cells if PROMPTS_MARK in c and "ROUTES = {" in c)
    catalog_cells = [next(c for c in cells if mark in c) for mark in CATALOG_MARKS]
    header = '"""{what}\n\nСгенерировано tools/extract_agent_data.py из ноутбука агента — не редактировать вручную."""\n# ruff: noqa\n'
    (OUT / "prompts.py").write_text(header.format(what="Промпты, маршруты проверки и коды факторов риска агента.") + prompts + "\n", encoding="utf-8")
    body = "import json\nimport re\nfrom typing import Any\n\n" + "\n\n".join(catalog_cells) + "\n"
    (OUT / "catalog.py").write_text(header.format(what="Каталог типов операций, матрица документов и промпты классификатора.") + body, encoding="utf-8")
    print("OK", OUT)


if __name__ == "__main__":
    main(Path(sys.argv[1] if len(sys.argv) > 1 else "/Users/stanislav/Documents/bank_agent/agent_v4_gc3_no_chains.ipynb"))
