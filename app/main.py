import asyncio
import logging

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

KEEPALIVE_INTERVAL_HOURS = 24


async def cookie_keepalive(nlm_client: NotebookLMWrapper):
    """Ping NotebookLM daily so Google keeps rotating our session cookies
    even when nobody is asking questions. Without this, an idle session
    expires after ~14 days."""
    while True:
        await asyncio.sleep(KEEPALIVE_INTERVAL_HOURS * 3600)
        try:
            await nlm_client.keepalive()
            logger.info("NotebookLM cookie keepalive succeeded")
        except Exception as e:
            logger.warning("NotebookLM cookie keepalive failed: %s", e)


async def main():
    logger.info("Loading configuration...")
    config = load_config()

    # Initialize the Slack app
    slack_app = AsyncApp(token=config.slack_bot_token)

    # Our own user ID, used to avoid double-handling messages that both
    # mention us and land in a tracked thread
    bot_user_id = None
    try:
        auth = await slack_app.client.auth_test()
        bot_user_id = auth.get("user_id")
        logger.info("Bot user ID: %s", bot_user_id)
    except Exception as e:
        logger.warning("Could not determine bot user ID: %s", e)

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
    register_handlers(slack_app, config, router, bot_user_id)
    logger.info("Reply style: %s", config.reply_style)

    # Keep Google session cookies fresh during idle periods
    keepalive_task = asyncio.create_task(cookie_keepalive(nlm_client))

    # Start Socket Mode
    handler = AsyncSocketModeHandler(slack_app, config.slack_app_token)
    logger.info("SlackLM bot starting in Socket Mode...")

    try:
        await handler.start_async()
    finally:
        keepalive_task.cancel()
        await nlm_client.close()


if __name__ == "__main__":
    asyncio.run(main())
