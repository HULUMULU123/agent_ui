"""Собирает finapp/parser/bank_parser.py из ячеек ноутбука bank_parser_v2_clean.ipynb.

    python tools/build_parser_module.py /path/to/bank_parser_v2_clean.ipynb

Ноутбук остаётся источником правды для алгоритма парсера. Скрипт берёт ячейки 1-14 (без
последней ячейки-запуска), убирает инициализацию GigaChat на уровне модуля (клиент передаётся
снаружи через configure()) и ставит модель GigaChat-3-Ultra.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "finapp" / "parser" / "bank_parser.py"

HEADER = '''"""Парсер банковских выписок v2 (сгенерировано tools/build_parser_module.py из bank_parser_v2_clean.ipynb).

Не редактируйте вручную: правки алгоритма вносятся в ноутбук, затем модуль пересобирается.
Модуль хранит состояние в глобальных переменных (CLOCK, LLM_CALLS) — вызывать только через
finapp.parser.service.parse_file, который сериализует запуски блокировкой.
"""
# ruff: noqa
'''

LLM_INIT = re.compile(r'GIGACHAT_MODEL = "GigaChat-2-Pro".*?print\("GigaChat:".*?\)\n', re.S)
LLM_STUB = '''GIGACHAT_MODEL = "GigaChat-3-Ultra"
LLM_CLIENT: Any | None = None
CACHE_PATH: str | None = None
LOG_HOOK = None
'''


def build(notebook: Path) -> str:
    cells = json.loads(notebook.read_text(encoding="utf-8"))["cells"]
    code = ["".join(c["source"]) for c in cells if c["cell_type"] == "code"]
    parts = code[:-1]  # последняя ячейка — пример запуска с display()
    first, n = LLM_INIT.subn(LLM_STUB, parts[0])
    if n != 1:
        raise SystemExit("Не найден блок инициализации GigaChat в первой ячейке")
    parts[0] = first
    text = "\n\n".join(parts)
    text = text.replace(
        'print(f"Агент недоступен (нет langgraph/langchain_core): {_agent_import_error}")', "pass")
    old_log = '    if VERBOSE:\n        print(f"[+{CLOCK.elapsed():6.1f} с][{stage}] {message}", flush=True)'
    if old_log not in text:
        raise SystemExit("Не найдена функция log()")
    text = text.replace(old_log, old_log + '\n    if LOG_HOOK is not None:\n        LOG_HOOK(f"[{stage}] {message}")')
    return HEADER + text + "\n"


if __name__ == "__main__":
    source = Path(sys.argv[1] if len(sys.argv) > 1 else "/Users/stanislav/Documents/bank_parser/bank_parser_v2_clean.ipynb")
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(build(source), encoding="utf-8")
    print("OK", TARGET, TARGET.stat().st_size)
