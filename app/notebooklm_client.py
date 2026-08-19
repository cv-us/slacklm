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


# Stall watchdog for the answer stream: abort when NO bytes arrive for this
# many consecutive seconds. This is a between-bytes timeout, not a total cap.
# NotebookLM delivers answers in bursts with long silent gaps while it works —
# healthy answers have shown ~43s of silence after connecting, and genuinely
# complex questions can pause longer — so 60s gives real answers headroom
# while still detecting a dead stream 3x faster than the library's 180s
# default.
CHAT_STALL_TIMEOUT_SECONDS = 60.0

# Cap per-source text handed to the Claude fallback: large PDFs (e.g. full
# code books) can exceed the model's context on their own.
MAX_SOURCE_CONTENT_CHARS = 150_000


class NotebookLMWrapper:
    def __init__(self):
        # Serialize client sessions: each session rotates Google cookies on
        # init, and concurrent rotations can invalidate each other's session.
        self._lock = asyncio.Lock()

    async def _run_with_client(self, func):
        async with self._lock:
            async with await NotebookLMClient.from_storage(
                chat_timeout=CHAT_STALL_TIMEOUT_SECONDS
            ) as client:
                return await func(client)

    async def close(self):
        pass

    async def keepalive(self) -> bool:
        """Open a session and make a trivial call so Google rotates the
        cookies. Keeps the stored session alive during idle periods (Google
        expires unused sessions after ~14 days)."""

        async def _ping(client):
            await client.notebooks.list()
            return True

        return await self._run_with_client(_ping)

    async def ask(self, notebook_id: str, question: str) -> Answer:
        async def _ask(client):
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

        try:
            return await self._run_with_client(_ask)
        except Exception as e:
            logger.error("NotebookLM chat.ask() failed: %s", e)
            raise

    async def list_sources(self, notebook_id: str) -> list[Source]:
        async def _list(client):
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

        try:
            return await self._run_with_client(_list)
        except Exception as e:
            logger.error("Failed to list sources: %s", e)
            raise

    async def get_source_content(self, notebook_id: str, source_id: str) -> str:
        async def _get(client):
            # get_fulltext returns the complete document text (PDFs included);
            # sources.get only returns metadata, so it's a last resort.
            if hasattr(client.sources, "get_fulltext"):
                fulltext = await client.sources.get_fulltext(notebook_id, source_id)
                content = getattr(fulltext, "content", "") or ""
            elif hasattr(client.sources, "get"):
                source = await client.sources.get(notebook_id, source_id)
                content = getattr(source, "content", getattr(source, "text", "")) or ""
            else:
                logger.warning("Source content retrieval not available")
                return ""
            if len(content) > MAX_SOURCE_CONTENT_CHARS:
                content = (
                    content[:MAX_SOURCE_CONTENT_CHARS]
                    + "\n\n[Content truncated — the document continues beyond this point.]"
                )
            return content

        try:
            return await self._run_with_client(_get)
        except Exception as e:
            logger.error("Failed to get source content: %s", e)
            raise
