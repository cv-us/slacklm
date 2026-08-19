import logging
import re

from app.config import Config, NotebookMapping, get_notebook_for_channel
from app.notebooklm_client import NotebookLMWrapper, Answer
from app.claude_client import ClaudeFallbackClient

logger = logging.getLogger(__name__)

OVERRIDE_PATTERN = re.compile(r"^in\s+([\w-]+)\s*:\s*(.+)$", re.IGNORECASE | re.DOTALL)


def parse_message(text: str, config: Config) -> tuple[str | None, str]:
    """Parse message for override syntax: 'in notebook-name: question'.

    Returns (notebook_name_override, question).
    """
    text = text.strip()
    match = OVERRIDE_PATTERN.match(text)
    if match:
        return match.group(1), match.group(2).strip()
    return None, text


def resolve_notebook(
    config: Config, channel_id: str, override_name: str | None
) -> NotebookMapping | None:
    if override_name:
        for mapping in config.channel_mappings.values():
            if mapping.name.lower().replace(" ", "-") == override_name.lower():
                return mapping
        if config.default_notebook and config.default_notebook.name.lower().replace(" ", "-") == override_name.lower():
            return config.default_notebook
        logger.warning("Override notebook '%s' not found, using channel default", override_name)

    return get_notebook_for_channel(config, channel_id)


class QueryRouter:
    def __init__(
        self,
        config: Config,
        nlm_client: NotebookLMWrapper,
        claude_client: ClaudeFallbackClient | None,
    ):
        self._config = config
        self._nlm = nlm_client
        self._claude = claude_client

    async def query(self, channel_id: str, raw_text: str) -> Answer:
        override_name, question = parse_message(raw_text, self._config)
        notebook = resolve_notebook(self._config, channel_id, override_name)

        if notebook is None:
            return Answer(
                text="No notebook is configured for this channel. Ask an admin to update `config/channels.yaml`.",
                engine="system",
            )

        # Try NotebookLM first, with one retry: transient server-side stalls
        # (answer stream never starts) are common enough that a second attempt
        # usually succeeds, and it's much better than dropping to the fallback.
        logger.info("Querying NotebookLM notebook '%s' (%s)", notebook.name, notebook.notebook_id)
        for attempt in (1, 2):
            try:
                answer = await self._nlm.ask(notebook.notebook_id, question)
                logger.info("NotebookLM answered successfully")
                return answer
            except Exception as e:
                if attempt == 1:
                    logger.warning("NotebookLM failed (attempt 1/2): %s. Retrying...", e)
                else:
                    logger.warning("NotebookLM failed after retry: %s. Trying Claude fallback...", e)

        # Try Claude fallback
        if self._claude and self._config.claude_fallback.enabled:
            try:
                logger.info("Querying Claude fallback for notebook '%s'", notebook.name)
                answer = await self._claude.ask(notebook.notebook_id, question)
                logger.info("Claude fallback answered successfully")
                return answer
            except Exception as e:
                logger.error("Claude fallback also failed: %s", e)

        return Answer(
            text="Sorry, I couldn't get an answer right now. Both NotebookLM and the Claude fallback are unavailable. Please try again later.",
            engine="error",
        )

    async def list_sources(self, channel_id: str) -> list[dict]:
        notebook = get_notebook_for_channel(self._config, channel_id)
        if notebook is None:
            return []
        try:
            sources = await self._nlm.list_sources(notebook.notebook_id)
            return [{"title": s.title, "source_id": s.source_id} for s in sources]
        except Exception as e:
            logger.error("Failed to list sources: %s", e)
            return []
