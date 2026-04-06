import asyncio
import logging
import sys

from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
from slack_bolt.async_app import AsyncApp

from app.config import load_config
from app.notebooklm_client import NotebookLMWrapper
from app.claude_client import ClaudeFallbackClient
from app.query_router import QueryRouter
from app.slack_handler import register_handlers

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def main():
    logger.info("Loading configuration...")
    config = load_config()

    # Initialize the Slack app
    slack_app = AsyncApp(token=config.slack_bot_token)

    # Initialize NotebookLM client
    nlm_client = NotebookLMWrapper()

    # Initialize Claude fallback (if API key is provided)
    claude_client = None
    if config.anthropic_api_key and config.claude_fallback.enabled:
        claude_client = ClaudeFallbackClient(
            api_key=config.anthropic_api_key,
            model=config.claude_fallback.model,
            nlm_client=nlm_client,
        )
        logger.info("Claude fallback enabled (model: %s)", config.claude_fallback.model)
    else:
        logger.info("Claude fallback disabled")

    # Set up query router
    router = QueryRouter(config, nlm_client, claude_client)

    # Register Slack event handlers
    register_handlers(slack_app, config, router)

    # Start Socket Mode
    handler = AsyncSocketModeHandler(slack_app, config.slack_app_token)
    logger.info("SlackLM bot starting in Socket Mode...")

    try:
        await handler.start_async()
    finally:
        await nlm_client.close()


if __name__ == "__main__":
    asyncio.run(main())
