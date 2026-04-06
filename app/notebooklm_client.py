import asyncio
import logging
from dataclasses import dataclass, field

from notebooklm import NotebookLMClient

logger = logging.getLogger(__name__)


@dataclass
class Source:
    source_id: str
    title: str


@dataclass
class Answer:
    text: str
    citations: list[str] = field(default_factory=list)
    sources: list[Source] = field(default_factory=list)
    engine: str = "notebooklm"


class NotebookLMWrapper:
    def __init__(self):
        self._client: NotebookLMClient | None = None

    async def _get_client(self) -> NotebookLMClient:
        if self._client is None:
            self._client = await NotebookLMClient.from_storage()
        return self._client

    async def close(self):
        if self._client is not None:
            await self._client.close()
            self._client = None

    async def ask(self, notebook_id: str, question: str) -> Answer:
        client = await self._get_client()
        try:
            response = await client.chat.ask(notebook_id, question)
            citations = []
            sources = []
            if hasattr(response, "citations") and response.citations:
                citations = [str(c) for c in response.citations]
            if hasattr(response, "sources") and response.sources:
                sources = [
                    Source(
                        source_id=getattr(s, "id", getattr(s, "source_id", str(i))),
                        title=getattr(s, "title", getattr(s, "name", f"Source {i + 1}")),
                    )
                    for i, s in enumerate(response.sources)
                ]
            answer_text = response.answer if hasattr(response, "answer") else str(response)
            return Answer(text=answer_text, citations=citations, sources=sources)
        except Exception as e:
            logger.error("NotebookLM chat.ask() failed: %s", e)
            raise

    async def list_sources(self, notebook_id: str) -> list[Source]:
        client = await self._get_client()
        try:
            notebooks = await client.notebooks.list()
            notebook = None
            for nb in notebooks:
                nb_id = getattr(nb, "id", getattr(nb, "notebook_id", None))
                if nb_id == notebook_id:
                    notebook = nb
                    break

            if notebook is None:
                logger.warning("Notebook %s not found", notebook_id)
                return []

            if hasattr(client, "sources") and hasattr(client.sources, "list"):
                raw_sources = await client.sources.list(notebook_id)
            elif hasattr(notebook, "sources"):
                raw_sources = notebook.sources
            else:
                logger.warning("Cannot list sources for notebook %s", notebook_id)
                return []

            return [
                Source(
                    source_id=getattr(s, "id", getattr(s, "source_id", str(i))),
                    title=getattr(s, "title", getattr(s, "name", f"Source {i + 1}")),
                )
                for i, s in enumerate(raw_sources)
            ]
        except Exception as e:
            logger.error("Failed to list sources: %s", e)
            raise

    async def get_source_content(self, notebook_id: str, source_id: str) -> str:
        client = await self._get_client()
        try:
            if hasattr(client, "sources") and hasattr(client.sources, "get"):
                source = await client.sources.get(notebook_id, source_id)
                return getattr(source, "content", getattr(source, "text", str(source)))
            else:
                logger.warning("Source content retrieval not available")
                return ""
        except Exception as e:
            logger.error("Failed to get source content: %s", e)
            raise
