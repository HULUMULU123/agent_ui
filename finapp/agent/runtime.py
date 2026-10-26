"""Runtime агента: клиенты GigaChat-3-Ultra, учёт токенов, параллельный запуск пакетов, вызов LLM с повторами."""
from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from .common import MODEL_NAME, AgentConfig

logger = logging.getLogger("finapp.agent.runtime")

try:  # langchain нужен только для реального GigaChat; тесты и режим «по правилам» работают без него
    from langchain_core.callbacks import BaseCallbackHandler
    from langchain_core.messages import HumanMessage, SystemMessage
except Exception:  # noqa: BLE001
    BaseCallbackHandler = object  # type: ignore[assignment,misc]
    HumanMessage = SystemMessage = None  # type: ignore[assignment]


def make_messages(system: str, human: str) -> list[Any]:
    if SystemMessage is not None:
        return [SystemMessage(content=system), HumanMessage(content=human)]
    return [("system", system), ("user", human)]


class TokenUsageCounter(BaseCallbackHandler):  # type: ignore[misc]
    """Суммирует usage_metadata всех ответов модели, включая вызовы через bind_tools."""

    def __init__(self, name: str):
        super().__init__()
        self.name = name
        self.calls = self.input_tokens = self.output_tokens = self.total_tokens = 0
        self._lock = threading.Lock()

    def on_llm_end(self, response, **kwargs) -> None:  # noqa: ANN001
        for generations in response.generations:
            for generation in generations:
                usage = getattr(getattr(generation, "message", None), "usage_metadata", None) or {}
                with self._lock:
                    self.calls += 1
                    self.input_tokens += usage.get("input_tokens", 0) or 0
                    self.output_tokens += usage.get("output_tokens", 0) or 0
                    self.total_tokens += usage.get("total_tokens", 0) or 0

    def as_dict(self) -> dict:
        return {"calls": self.calls, "input": self.input_tokens, "output": self.output_tokens, "total": self.total_tokens}


@dataclass
class GigaChatSettings:
    """Подключение к GigaChat. Либо base_url + сертификаты (корпоративный контур), либо credentials (API ключ)."""

    base_url: str = ""
    cert_file: str = "certs/cert.pem"
    key_file: str = "certs/private.key"
    verify_ssl: bool = False
    credentials: str = ""
    scope: str = "GIGACHAT_API_CORP"
    model: str = MODEL_NAME
    timeout: float = 300.0

    @property
    def configured(self) -> bool:
        return bool(self.base_url.strip().lower().startswith(("http://", "https://")) or self.credentials.strip())


def build_gigachat(settings: GigaChatSettings, callbacks: list | None = None, timeout: float | None = None) -> Any:
    """Клиент langchain_gigachat.GigaChat для GigaChat-3-Ultra. ImportError, если пакет не установлен."""
    from langchain_gigachat.chat_models import GigaChat

    common = dict(model=settings.model or MODEL_NAME, temperature=1e-8, profanity_check=False,
                  timeout=timeout or settings.timeout, verify_ssl_certs=settings.verify_ssl)
    if callbacks:
        common["callbacks"] = callbacks
    if settings.base_url.strip().lower().startswith(("http://", "https://")):
        return GigaChat(base_url=settings.base_url, cert_file=settings.cert_file, key_file=settings.key_file, **common)
    if settings.credentials.strip():
        return GigaChat(credentials=settings.credentials, scope=settings.scope, **common)
    raise ValueError("GigaChat не настроен: укажите base_url с сертификатами или credentials.")


def build_embeddings(settings: GigaChatSettings) -> Any | None:
    try:
        from langchain_gigachat.embeddings import GigaChatEmbeddings
    except Exception:  # noqa: BLE001
        return None
    kwargs: dict[str, Any] = {"verify_ssl_certs": settings.verify_ssl}
    if settings.base_url.strip().lower().startswith(("http://", "https://")):
        kwargs.update(base_url=settings.base_url, cert_file=settings.cert_file, key_file=settings.key_file)
    elif settings.credentials.strip():
        kwargs.update(credentials=settings.credentials, scope=settings.scope)
    else:
        return None
    try:
        return GigaChatEmbeddings(**kwargs)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Эмбеддинги GigaChat недоступны: %s", exc)
        return None


@dataclass
class Runtime:
    """Всё, что агенту нужно извне: модели, инструменты, конфиг. llm=None -> режим «по правилам» без LLM."""

    cfg: AgentConfig
    llm: Any | None = None            # оркестратор и анализатор
    llm_helper: Any | None = None     # классификатор типов, правовой поиск, отбор материалов, ремонт JSON
    embeddings: Any | None = None
    risk_db: Any | None = None
    retriever: Any | None = None
    practice_tool: Any | None = None
    usage_llm: TokenUsageCounter = field(default_factory=lambda: TokenUsageCounter("llm"))
    usage_helper: TokenUsageCounter = field(default_factory=lambda: TokenUsageCounter("llm_helper"))
    log: Callable[[str], None] = lambda message: None
    _orchestrator_llm: Any | None = None

    @property
    def use_llm(self) -> bool:
        return self.llm is not None

    @property
    def helper(self) -> Any | None:
        return self.llm_helper or self.llm

    def orchestrator_llm(self) -> Any:
        """LLM с нативными вызовами инструментов, если клиент это поддерживает; иначе обычный (инструменты — через JSON)."""
        if self._orchestrator_llm is None:
            from .tools import TOOL_SCHEMAS
            try:
                self._orchestrator_llm = self.llm.bind_tools(TOOL_SCHEMAS)
            except Exception as exc:  # noqa: BLE001
                logger.info("bind_tools недоступен (%s): инструменты запрашиваются через JSON-поле requested_tools.", exc)
                self._orchestrator_llm = self.llm
        return self._orchestrator_llm

    def token_summary(self) -> dict:
        return {"llm": self.usage_llm.as_dict(), "llm_helper": self.usage_helper.as_dict(),
                "total": self.usage_llm.total_tokens + self.usage_helper.total_tokens}


def invoke_llm(model: Any, messages: list, *, attempts: int = 3, role: str = "llm") -> Any:
    """Вызов модели с повтором при временных ошибках (429/503/таймаут): паузы 2 и 6 секунд."""
    last: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return model.invoke(messages)
        except Exception as exc:  # noqa: BLE001
            last = exc
            logger.warning("%s: ошибка вызова LLM (попытка %d/%d): %s", role, attempt, attempts, exc)
            if attempt < attempts:
                time.sleep(2 * 3 ** (attempt - 1))
    assert last is not None
    raise last


def run_batches_parallel(items: Iterable, worker: Callable, desc: str, max_workers: int,
                         on_done: Callable[[int, int], None] | None = None) -> list:
    """worker(item) для всех items с сохранением порядка; упавший элемент -> None. on_done(done, total) — прогресс."""
    items = list(items)
    if not items:
        return []
    workers = max(1, int(max_workers))
    done = 0
    lock = threading.Lock()

    def _guarded(position_and_item):
        nonlocal done
        position, item = position_and_item
        try:
            result = worker(item)
        except Exception:  # noqa: BLE001
            logger.exception("%s: элемент %d завершился ошибкой", desc, position)
            result = None
        with lock:
            done += 1
            if on_done is not None:
                on_done(done, len(items))
        return position, result

    results: list = [None] * len(items)
    if workers == 1:
        for pair in enumerate(items):
            position, result = _guarded(pair)
            results[position] = result
        return results
    with ThreadPoolExecutor(max_workers=workers) as executor:
        for position, result in executor.map(_guarded, enumerate(items)):
            results[position] = result
    return results
