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

# Google's __Secure-1PSIDTS freshness token must be re-rotated on a ~600s
# cadence (RotateCookies itself advertises 600s); a session left unrotated
# for a few hours dies server-side. Each question rotates it too — this
# loop covers the idle stretches. Keep between 600-900s; never below 60s.
KEEPALIVE_INTERVAL_SECONDS = 600
KEEPALIVE_ALERT_AFTER_FAILURES = 2


async def cookie_keepalive(
    nlm_client: NotebookLMWrapper,
    slack_client=None,
    admin_channel: str | None = None,
):
    """Rotate the Google session cookies every few minutes so the session
    survives idle periods. Pings immediately on startup (covers container
    downtime), then on the interval. If rotation starts failing, the session
    is dying — optionally alert a Slack admin channel while there may still
    be time to re-auth."""
    consecutive_failures = 0
    alerted = False
    while True:
        try:
            await nlm_client.keepalive()
            logger.info("NotebookLM cookie keepalive succeeded")
            if alerted and slack_client and admin_channel:
                try:
                    await slack_client.chat_postMessage(
                        channel=admin_channel,
                        text=":white_check_mark: SlackLM: NotebookLM session keepalive recovered.",
                    )
                except Exception as e:
                    logger.warning("Could not post keepalive recovery alert: %s", e)
            consecutive_failures = 0
            alerted = False
        except Exception as e:
            consecutive_failures += 1
            logger.warning(
                "NotebookLM cookie keepalive failed (%d consecutive): %s",
                consecutive_failures,
                e,
            )
            if (
                consecutive_failures >= KEEPALIVE_ALERT_AFTER_FAILURES
                and not alerted
                and slack_client
                and admin_channel
            ):
                try:
                    await slack_client.chat_postMessage(
                        channel=admin_channel,
                        text=(
                            ":warning: SlackLM: NotebookLM session keepalive has failed "
                            f"{consecutive_failures} times in a row — the Google session may be dying. "
                            "Re-authenticate soon: run `notebooklm login` locally, copy "
                            "`storage_state.json` to `~/slacklm/notebooklm-profile/` on the server, "
                            "then `docker compose restart`."
                        ),
                    )
                    alerted = True
                except Exception as alert_err:
                    logger.warning("Could not post keepalive failure alert: %s", alert_err)
        await asyncio.sleep(KEEPALIVE_INTERVAL_SECONDS)


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
    keepalive_task = asyncio.create_task(
        cookie_keepalive(nlm_client, slack_app.client, config.admin_channel)
    )

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
