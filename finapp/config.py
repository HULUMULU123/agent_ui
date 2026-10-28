"""Настройки приложения: пути, подключение к GigaChat-3-Ultra, параметры анализа. Хранятся в data/settings.json."""
from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any

from .agent.common import MODEL_NAME, AgentConfig
from .agent.runtime import GigaChatSettings

logger = logging.getLogger("finapp.config")

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("FINAPP_DATA_DIR", ROOT / "data"))
SETTINGS_PATH = DATA_DIR / "settings.json"
RISK_DB_PATH = Path(os.getenv("RISK_MEMORY_DB_PATH", ROOT / "risk_memory.db"))

try:  # .env необязателен
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except Exception:  # noqa: BLE001
    pass


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    return default if value is None else value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class AppSettings:
    """Всё, что сотрудник или администратор может поменять на странице «Настройки»."""

    # подключение к GigaChat (модель фиксирована: GigaChat-3-Ultra)
    base_url: str = ""
    cert_file: str = "certs/cert.pem"
    key_file: str = "certs/private.key"
    verify_ssl: bool = False
    credentials: str = ""
    scope: str = "GIGACHAT_API_CORP"
    # режим: "auto" — GigaChat, если настроен; "rules" — без LLM (предварительная оценка по правилам)
    mode: str = "auto"
    # параметры анализа
    llm_max_workers: int = 2
    max_operations_per_llm_batch: int = 10
    rule_skip_older_than_years: int = 3
    default_filing_date: str = ""

    @classmethod
    def from_env(cls) -> "AppSettings":
        return cls(
            base_url=os.getenv("GIGACHAT_BASE_URL", ""),  # пусто -> корпоративный адрес по умолчанию (см. GigaChatSettings)
            cert_file=os.getenv("GIGACHAT_CERT_FILE", "certs/cert.pem"),
            key_file=os.getenv("GIGACHAT_KEY_FILE", "certs/private.key"),
            verify_ssl=_env_bool("GIGACHAT_VERIFY_SSL", False),
            credentials=os.getenv("GIGACHAT_CREDENTIALS", ""),
            scope=os.getenv("GIGACHAT_SCOPE", "GIGACHAT_API_CORP"),
            mode=os.getenv("FINAPP_MODE", "auto"),
        )

    def gigachat(self) -> GigaChatSettings:
        def resolve(path: str) -> str:
            p = Path(path).expanduser()
            return str(p if p.is_absolute() else ROOT / p)
        return GigaChatSettings(base_url=self.base_url.strip(), cert_file=resolve(self.cert_file),
                                key_file=resolve(self.key_file), verify_ssl=self.verify_ssl,
                                credentials=self.credentials.strip(), scope=self.scope.strip() or "GIGACHAT_API_CORP",
                                model=MODEL_NAME)

    @property
    def llm_enabled(self) -> bool:
        return self.mode != "rules" and self.gigachat().configured

    def agent_config(self, filing_date) -> AgentConfig:  # noqa: ANN001
        return AgentConfig(
            court_filing_date=filing_date, llm_max_workers=max(1, int(self.llm_max_workers)),
            max_operations_per_llm_batch=max(1, int(self.max_operations_per_llm_batch)),
            rule_skip_older_than_years=int(self.rule_skip_older_than_years) or None)


class SettingsStore:
    """Настройки процесса: env -> data/settings.json (поверх env). Секреты на диске — с правами 0600."""

    def __init__(self, path: Path = SETTINGS_PATH):
        self.path = path
        self._lock = threading.Lock()
        self._settings = self._load()

    def _load(self) -> AppSettings:
        settings = AppSettings.from_env()
        try:
            stored = json.loads(self.path.read_text(encoding="utf-8"))
            known = {f.name for f in fields(AppSettings)}
            for key, value in stored.items():
                if key in known:
                    setattr(settings, key, value)
        except (OSError, ValueError):
            pass
        return settings

    def get(self) -> AppSettings:
        with self._lock:
            return AppSettings(**asdict(self._settings))

    def update(self, **values: Any) -> AppSettings:
        with self._lock:
            known = {f.name for f in fields(AppSettings)}
            for key, value in values.items():
                if key in known:
                    setattr(self._settings, key, value)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(asdict(self._settings), ensure_ascii=False, indent=2), encoding="utf-8")
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass
            return AppSettings(**asdict(self._settings))


SETTINGS = SettingsStore()
