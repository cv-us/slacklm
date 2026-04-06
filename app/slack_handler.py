import asyncio
import logging
import re

from slack_bolt.async_app import AsyncApp

from app.config import Config
from app.query_router import QueryRouter
from app.formatter import format_answer, format_sources_list, format_help

logger = logging.getLogger(__name__)

BOT_MENTION_PATTERN = re.compile(r"<@[\w]+>\s*")


def strip_bot_mention(text: str) -> str:
    """Remove the @bot mention prefix from a message."""
    return BOT_MENTION_PATTERN.sub("", text, count=1).strip()


def register_handlers(app: AsyncApp, config: Config, router: QueryRouter):
    @app.event("app_mention")
    async def handle_mention(event, say, client):
        text = strip_bot_mention(event.get("text", ""))
        channel = event.get("channel", "")
        thread_ts = event.get("thread_ts") or event.get("ts")

        if not text:
            await say(
                text="How can I help? Try `@SlackLM help` for usage info.",
                thread_ts=thread_ts,
            )
            return

        command = text.lower().strip()

        if command == "help":
            blocks = format_help()
            await say(blocks=blocks, text="SlackLM Help", thread_ts=thread_ts)
            return

        if command == "sources":
            sources = await router.list_sources(channel)
            blocks = format_sources_list(sources)
            await say(blocks=blocks, text="Notebook Sources", thread_ts=thread_ts)
            return

        # Post a "thinking" message
        thinking = await client.chat_postMessage(
            channel=channel,
            thread_ts=thread_ts,
            text="Thinking...",
        )

        try:
            answer = await router.query(channel, text)
            blocks = format_answer(answer)

            # Update the thinking message with the actual answer
            await client.chat_update(
                channel=channel,
                ts=thinking["ts"],
                blocks=blocks,
                text=answer.text[:200],
            )
        except Exception as e:
            logger.error("Failed to process question: %s", e)
            await client.chat_update(
                channel=channel,
                ts=thinking["ts"],
                text="Sorry, something went wrong while processing your question. Please try again.",
            )

    @app.event("message")
    async def handle_dm(event, say, client):
        # Only handle direct messages (no subtype = regular user message)
        if event.get("channel_type") != "im":
            return
        if event.get("subtype"):
            return
        # Ignore bot's own messages
        if event.get("bot_id"):
            return

        text = strip_bot_mention(event.get("text", "")).strip()
        channel = event.get("channel", "")
        thread_ts = event.get("thread_ts") or event.get("ts")

        if not text:
            await say(
                text="How can I help? Type `help` for usage info.",
                thread_ts=thread_ts,
            )
            return

        command = text.lower().strip()

        if command == "help":
            blocks = format_help()
            await say(blocks=blocks, text="SlackLM Help", thread_ts=thread_ts)
            return

        if command == "sources":
            sources = await router.list_sources(channel)
            blocks = format_sources_list(sources)
            await say(blocks=blocks, text="Notebook Sources", thread_ts=thread_ts)
            return

        # Post thinking message
        thinking = await client.chat_postMessage(
            channel=channel,
            thread_ts=thread_ts,
            text="Thinking...",
        )

        try:
            # DMs use the default notebook (no channel mapping)
            answer = await router.query(channel, text)
            blocks = format_answer(answer)

            await client.chat_update(
                channel=channel,
                ts=thinking["ts"],
                blocks=blocks,
                text=answer.text[:200],
            )
        except Exception as e:
            logger.error("Failed to process DM question: %s", e)
            await client.chat_update(
                channel=channel,
                ts=thinking["ts"],
                text="Sorry, something went wrong. Please try again.",
            )
