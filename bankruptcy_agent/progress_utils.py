from __future__ import annotations

from typing import Callable, Optional

# (message, percent_0_to_100)
StatusCallback = Callable[[str, int], None]

TOOL_FRIENDLY_NAMES = {
    "get_conterparty_risk": "запрашиваю историю риска контрагента (память агента по прошлым операциям)",
    "search_practice_and_normative": "ищу подходящую нормативную базу и судебную практику",
}


def friendly_tool_message(tool_name: str) -> str:
    return TOOL_FRIENDLY_NAMES.get(tool_name, f"использую инструмент «{tool_name}»" if tool_name else "использую дополнительный инструмент анализа")


class ProgressReporter:
    """Переводит ход анализа в дружелюбные сообщения для UI сотрудника.

    Держит долю общего прогресса [start, end] (в процентах 0-100), которую
    занимает текущий этап пайплайна, и переводит внутренний прогресс
    подэтапа (0.0-1.0) в абсолютный процент. Совместим с вызовом в стиле
    gr.Progress (`reporter(fraction, desc=...)`), поэтому его можно
    передавать напрямую туда, где раньше ожидался прогресс-колбэк тетрадки.
    """

    def __init__(self, callback: Optional[StatusCallback], start: float = 0.0, end: float = 100.0):
        self._callback = callback
        self._start = start
        self._end = end
        self._last_percent = int(round(start))

    def __call__(self, fraction: float, desc: str = "") -> None:
        self.report(desc, fraction)

    def report(self, message: str, fraction: float = 0.0) -> None:
        fraction = min(max(fraction, 0.0), 1.0)
        percent = int(round(self._start + (self._end - self._start) * fraction))
        self._last_percent = percent
        if self._callback is not None and message:
            self._callback(message, percent)

    def note(self, message: str) -> None:
        """Сообщение внутри уже отчитанного шага — процент не меняется."""
        if self._callback is not None and message:
            self._callback(message, self._last_percent)

    def sub(self, start_percent: float, end_percent: float) -> "ProgressReporter":
        reporter = ProgressReporter(self._callback, start_percent, end_percent)
        return reporter
