import asyncio
import logging
import time
from collections import OrderedDict
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

# Navigation windows for the Claude fallback: the head of a document usually
# carries the cover + table of contents, the tail usually carries the index.
OVERVIEW_HEAD_CHARS = 40_000
OVERVIEW_TAIL_CHARS = 25_000
SECTION_READ_DEFAULT_CHARS = 50_000
SECTION_READ_MAX_CHARS = 80_000

# Fetching fulltext pulls the ENTIRE document from NotebookLM, so cache it
# briefly: one Q&A session does several targeted reads of the same document.
_FULLTEXT_CACHE_MAX_ENTRIES = 4
_FULLTEXT_CACHE_TTL_SECONDS = 600.0


def make_overview(
    content: str,
    head_chars: int = OVERVIEW_HEAD_CHARS,
    tail_chars: int = OVERVIEW_TAIL_CHARS,
) -> dict:
    """Head (cover/TOC region) + tail (index region) + total size."""
    total = len(content)
    if total <= head_chars + tail_chars:
        return {"char_count": total, "head": content, "tail": ""}
    return {
        "char_count": total,
        "head": content[:head_chars],
        "tail": content[-tail_chars:],
    }


def slice_window(content: str, start_char: int, length: int) -> dict:
    """A bounded window of the document, with clamped coordinates."""
    total = len(content)
    length = max(1, min(int(length), SECTION_READ_MAX_CHARS))
    start = max(0, min(int(start_char), max(total - 1, 0)))
    end = min(total, start + length)
    return {
        "char_count": total,
        "start_char": start,
        "end_char": end,
        "content": content[start:end],
    }


class NotebookLMWrapper:
    def __init__(self):
        # Serialize client sessions: each session rotates Google cookies on
        # init, and concurrent rotations can invalidate each other's session.
        self._lock = asyncio.Lock()
        self._fulltext_cache: OrderedDict[tuple[str, str], tuple[float, str]] = OrderedDict()

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

    async def _get_fulltext(self, notebook_id: str, source_id: str) -> str:
        """Full document text, briefly cached so one Q&A session's several
        targeted reads of the same document fetch it only once."""
        key = (notebook_id, source_id)
        now = time.monotonic()
        cached = self._fulltext_cache.get(key)
        if cached is not None and now - cached[0] < _FULLTEXT_CACHE_TTL_SECONDS:
            self._fulltext_cache.move_to_end(key)
            return cached[1]

        async def _get(client):
            # get_fulltext returns the complete document text (PDFs included);
            # sources.get only returns metadata, so it's a last resort.
            if hasattr(client.sources, "get_fulltext"):
                fulltext = await client.sources.get_fulltext(notebook_id, source_id)
                return getattr(fulltext, "content", "") or ""
            if hasattr(client.sources, "get"):
                source = await client.sources.get(notebook_id, source_id)
                return getattr(source, "content", getattr(source, "text", "")) or ""
            logger.warning("Source content retrieval not available")
            return ""

        content = await self._run_with_client(_get)
        self._fulltext_cache[key] = (now, content)
        self._fulltext_cache.move_to_end(key)
        while len(self._fulltext_cache) > _FULLTEXT_CACHE_MAX_ENTRIES:
            self._fulltext_cache.popitem(last=False)
        return content

    async def get_source_overview(self, notebook_id: str, source_id: str) -> dict:
        """Total size + the document's beginning (cover/TOC) and end (index)."""
        try:
            content = await self._get_fulltext(notebook_id, source_id)
            return make_overview(content)
        except Exception as e:
            logger.error("Failed to get source overview: %s", e)
            raise

    async def read_source_section(
        self,
        notebook_id: str,
        source_id: str,
        start_char: int,
        length: int = SECTION_READ_DEFAULT_CHARS,
    ) -> dict:
        """A targeted window of the document at an arbitrary offset."""
        try:
            content = await self._get_fulltext(notebook_id, source_id)
            return slice_window(content, start_char, length)
        except Exception as e:
            logger.error("Failed to read source section: %s", e)
            raise

    async def get_source_content(self, notebook_id: str, source_id: str) -> str:
        try:
            content = await self._get_fulltext(notebook_id, source_id)
        except Exception as e:
            logger.error("Failed to get source content: %s", e)
            raise
        if len(content) > MAX_SOURCE_CONTENT_CHARS:
            content = (
                content[:MAX_SOURCE_CONTENT_CHARS]
                + "\n\n[Content truncated — the document continues beyond this point.]"
            )
        return content
