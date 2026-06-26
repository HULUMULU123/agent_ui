from __future__ import annotations

from pathlib import Path
from typing import Any


def build_normative_retriever(embeddings: Any, index_path: str = "./faiss_normative_db"):
    """Загрузка FAISS-индекса нормативной базы из тетрадки.

    Требует дополнительных пакетов `langchain-community` и `faiss-cpu`.
    Используйте только если хотите собирать retriever внутри проекта.
    """
    from langchain_core.documents import Document
    from langchain_community.vectorstores import FAISS

    index_dir = Path(index_path)
    if (index_dir / "index.faiss").exists():
        return FAISS.load_local(str(index_dir), embeddings, allow_dangerous_deserialization=True)

    index_dir.mkdir(parents=True, exist_ok=True)
    dummy_doc = Document(
        page_content="Инициализационный документ. Замените индекс реальной нормативной базой.",
        metadata={"source": "dummy"},
    )
    vectorstore = FAISS.from_documents([dummy_doc], embeddings)
    vectorstore.save_local(str(index_dir))
    return vectorstore
