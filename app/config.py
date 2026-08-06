import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import load_dotenv


@dataclass
class NotebookMapping:
    notebook_id: str
    name: str


@dataclass
class ClaudeFallbackConfig:
    enabled: bool = True
    model: str = "claude-sonnet-4-6"


@dataclass
class Config:
    slack_bot_token: str
    slack_app_token: str
    anthropic_api_key: str
    channel_mappings: dict[str, NotebookMapping] = field(default_factory=dict)
    default_notebook: NotebookMapping | None = None
    claude_fallback: ClaudeFallbackConfig = field(default_factory=ClaudeFallbackConfig)
    # "thread": reply in a thread under the user's message (default)
    # "channel": post the answer as a regular top-level channel message
    reply_style: str = "thread"
    # When True, replies in threads the bot has answered are auto-answered
    # without an @mention. Off by default so only explicit @mentions (and
    # DMs) consume NotebookLM/Claude usage.
    auto_thread_replies: bool = False
    # Optional Slack channel ID for operational alerts (e.g., the Google
    # session is dying and needs re-auth). The bot must be in the channel.
    admin_channel: str | None = None


def load_config(
    config_path: str | Path = "config/channels.yaml",
) -> Config:
    load_dotenv()

    slack_bot_token = os.environ.get("SLACK_BOT_TOKEN", "")
    slack_app_token = os.environ.get("SLACK_APP_TOKEN", "")
    anthropic_api_key = os.environ.get("ANTHROPIC_API_KEY", "")

    if not slack_bot_token:
        raise ValueError("SLACK_BOT_TOKEN environment variable is required")
    if not slack_app_token:
        raise ValueError("SLACK_APP_TOKEN environment variable is required")

    channel_mappings: dict[str, NotebookMapping] = {}
    default_notebook: NotebookMapping | None = None
    claude_fallback = ClaudeFallbackConfig()
    reply_style = "thread"
    auto_thread_replies = False
    admin_channel = None

    config_file = Path(config_path)
    if config_file.exists():
        with open(config_file) as f:
            raw = yaml.safe_load(f) or {}

        channels = raw.get("channels", {})
        for channel_id, mapping in channels.items():
            nb = NotebookMapping(
                notebook_id=mapping["notebook_id"],
                name=mapping.get("name", ""),
            )
            if channel_id == "default":
                default_notebook = nb
            else:
                channel_mappings[channel_id] = nb

        fallback_raw = raw.get("claude_fallback", {})
        if fallback_raw:
            claude_fallback = ClaudeFallbackConfig(
                enabled=fallback_raw.get("enabled", True),
                model=fallback_raw.get("model", "claude-sonnet-4-6"),
            )

        reply_style = raw.get("reply_style", "thread")
        if reply_style not in ("thread", "channel"):
            reply_style = "thread"

        auto_thread_replies = bool(raw.get("auto_thread_replies", False))
        admin_channel = raw.get("admin_channel") or None

    return Config(
        slack_bot_token=slack_bot_token,
        slack_app_token=slack_app_token,
        anthropic_api_key=anthropic_api_key,
        channel_mappings=channel_mappings,
        default_notebook=default_notebook,
        claude_fallback=claude_fallback,
        reply_style=reply_style,
        auto_thread_replies=auto_thread_replies,
        admin_channel=admin_channel,
    )


def get_notebook_for_channel(config: Config, channel_id: str) -> NotebookMapping | None:
    return config.channel_mappings.get(channel_id, config.default_notebook)
