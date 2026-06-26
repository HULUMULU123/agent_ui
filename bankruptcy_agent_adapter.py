"""Backward-compatible wrapper.

Реальная агентная логика теперь разнесена по пакету `bankruptcy_agent/`:
config.py, schemas.py, models.py, io.py, preprocessing.py, fallback.py,
extraction.py, output_normalizer.py, dashboard.py, pipeline.py.

Файл оставлен только чтобы старые импорты `from bankruptcy_agent_adapter import ...`
не ломали существующие ноутбуки и скрипты.
"""

from bankruptcy_agent import *  # noqa: F401,F403
from bankruptcy_agent.dashboard import *  # noqa: F401,F403
from bankruptcy_agent.extraction import *  # noqa: F401,F403
from bankruptcy_agent.fallback import *  # noqa: F401,F403
from bankruptcy_agent.io import *  # noqa: F401,F403
from bankruptcy_agent.output_normalizer import *  # noqa: F401,F403
from bankruptcy_agent.pipeline import *  # noqa: F401,F403
from bankruptcy_agent.preprocessing import *  # noqa: F401,F403
from bankruptcy_agent.real_agent_bridge import *  # noqa: F401,F403
from bankruptcy_agent.utils import *  # noqa: F401,F403
