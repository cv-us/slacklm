import logging
import re
from collections import OrderedDict

from slack_bolt.async_app import AsyncApp

from app.config import Config
from app.query_router import QueryRouter
from app.formatter import format_answer, format_sources_list, format_help

logger = logging.getLogger(__name__)

BOT_MENTION_PATTERN = re.compile(r"<@[\w]+>\s*")


def strip_bot_mention(text: str) -> str:
    """Remove the @bot mention prefix from a message."""
    return BOT_MENTION_PATTERN.sub("", text, count=1).strip()


class ThreadTracker:
    """Remembers threads the bot has answered in so follow-up replies in
    those threads can be answered without requiring an @mention.

    In-memory only: after a restart, old threads need a fresh @mention.
    Bounded so long-running bots don't grow without limit.
    """

    def __init__(self, max_size: int = 1000):
        self._threads: OrderedDict[tuple[str, str], None] = OrderedDict()
        self._max_size = max_size

    def track(self, channel: str, thread_ts: str):
        key = (channel, thread_ts)
        self._threads[key] = None
        self._threads.move_to_end(key)
        while len(self._threads) > self._max_size:
            self._threads.popitem(last=False)

    def is_tracked(self, channel: str, thread_ts: str) -> bool:
        return (channel, thread_ts) in self._threads


def register_handlers(
    app: AsyncApp,
    config: Config,
    router: QueryRouter,
    bot_user_id: str | None = None,
):
    tracker = ThreadTracker()

    async def respond(client, channel: str, text: str, thread_ts: str | None) -> str:
        """Answer a question or command. thread_ts=None posts top-level.

        Returns the ts of the posted message (usable as a thread root).
        """
        command = text.lower().strip()

        if command == "help":
            resp = await client.chat_postMessage(
                channel=channel,
                thread_ts=thread_ts,
                blocks=format_help(),
                text="SlackLM Help",
            )
            return resp["ts"]

        if command == "sources":
            sources = await router.list_sources(channel)
            resp = await client.chat_postMessage(
                channel=channel,
                thread_ts=thread_ts,
                blocks=format_sources_list(sources),
                text="Notebook Sources",
            )
            return resp["ts"]

        thinking = await client.chat_postMessage(
            channel=channel,
            thread_ts=thread_ts,
            text="Thinking...",
        )
        try:
            answer = await router.query(channel, text)
            blocks = format_answer(answer)
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
        return thinking["ts"]

    @app.event("app_mention")
    async def handle_mention(event, client):
        text = strip_bot_mention(event.get("text", ""))
        channel = event.get("channel", "")
        existing_thread = event.get("thread_ts")

        if not text:
            await client.chat_postMessage(
                channel=channel,
                thread_ts=existing_thread or event.get("ts"),
                text="How can I help? Try `@SlackLM help` for usage info.",
            )
            return

        if existing_thread:
            # Mentioned inside an existing thread: stay in that thread and
            # answer follow-ups there from now on.
            await respond(client, channel, text, existing_thread)
            tracker.track(channel, existing_thread)
        elif config.reply_style == "channel":
            # Post the answer as a regular top-level channel message. Replies
            # threaded onto that message become follow-ups.
            posted_ts = await respond(client, channel, text, None)
            tracker.track(channel, posted_ts)
        else:
            # Default: reply in a thread under the user's message.
            thread_ts = event.get("ts")
            await respond(client, channel, text, thread_ts)
            tracker.track(channel, thread_ts)

    @app.event("message")
    async def handle_message(event, client):
        # Ignore message edits/joins/etc. and anything from bots (including us)
        if event.get("subtype") or event.get("bot_id"):
            return

        channel = event.get("channel", "")
        channel_type = event.get("channel_type")
        raw_text = event.get("text", "")

        # Direct messages: every message is a question, no mention needed
        if channel_type == "im":
            text = strip_bot_mention(raw_text).strip()
            thread_ts = event.get("thread_ts") or event.get("ts")
            if not text:
                await client.chat_postMessage(
                    channel=channel,
                    thread_ts=thread_ts,
                    text="How can I help? Type `help` for usage info.",
                )
                return
            await respond(client, channel, text, thread_ts)
            return

        # Channel messages: only auto-answer replies in threads we're part of.
        # (Requires the message.channels event subscription + channels:history
        # scope; without them Slack never delivers these events.)
        thread_ts = event.get("thread_ts")
        if not thread_ts or not tracker.is_tracked(channel, thread_ts):
            return
        if bot_user_id and f"<@{bot_user_id}>" in raw_text:
            return  # explicit mention: the app_mention handler covers it
        text = strip_bot_mention(raw_text).strip()
        if not text:
            return
        await respond(client, channel, text, thread_ts)
