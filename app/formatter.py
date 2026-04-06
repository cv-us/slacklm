from app.notebooklm_client import Answer

MAX_TEXT_BLOCK_LENGTH = 3000


def format_answer(answer: Answer) -> list[dict]:
    """Format an Answer into Slack Block Kit blocks."""
    blocks = []

    # Split answer text into chunks if needed (Slack 3000 char limit per block)
    text = answer.text.strip()
    chunks = _split_text(text, MAX_TEXT_BLOCK_LENGTH)

    for chunk in chunks:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": chunk},
        })

    # Citations section
    if answer.citations:
        citations_text = "\n".join(f"• {c}" for c in answer.citations[:10])
        blocks.append({"type": "divider"})
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Citations:*\n{citations_text}",
            },
        })

    # Sources section
    if answer.sources:
        source_names = ", ".join(f"_{s.title}_" for s in answer.sources[:10])
        blocks.append({
            "type": "context",
            "elements": [
                {"type": "mrkdwn", "text": f"Sources: {source_names}"},
            ],
        })

    # Engine indicator
    engine_labels = {
        "notebooklm": "Answered by NotebookLM",
        "claude": "Answered by Claude (fallback)",
        "system": "System message",
        "error": "Error",
    }
    engine_label = engine_labels.get(answer.engine, answer.engine)
    blocks.append({
        "type": "context",
        "elements": [
            {"type": "mrkdwn", "text": f"_{engine_label}_"},
        ],
    })

    return blocks


def format_sources_list(sources: list[dict]) -> list[dict]:
    """Format a list of sources for the @bot sources command."""
    if not sources:
        return [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "No sources found for this channel's notebook.",
                },
            }
        ]

    items = "\n".join(f"• {s['title']}" for s in sources)
    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Sources in this notebook:*\n{items}",
            },
        }
    ]


def format_help() -> list[dict]:
    """Format the help message."""
    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    "*SlackLM — NotebookLM Knowledge Bot*\n\n"
                    "*Usage:*\n"
                    "• `@SlackLM <question>` — Ask a question using this channel's notebook\n"
                    "• `@SlackLM in <notebook-name>: <question>` — Ask using a specific notebook\n"
                    "• `@SlackLM sources` — List sources in this channel's notebook\n"
                    "• `@SlackLM help` — Show this help message\n\n"
                    "Questions are answered from PDF sources uploaded to NotebookLM. "
                    "If NotebookLM is unavailable, Claude AI provides a fallback using the same sources."
                ),
            },
        }
    ]


def _split_text(text: str, max_length: int) -> list[str]:
    """Split text into chunks respecting the max length, splitting at paragraph boundaries."""
    if len(text) <= max_length:
        return [text]

    chunks = []
    remaining = text
    while remaining:
        if len(remaining) <= max_length:
            chunks.append(remaining)
            break

        # Try to split at a paragraph break
        split_at = remaining.rfind("\n\n", 0, max_length)
        if split_at == -1:
            # Try a single newline
            split_at = remaining.rfind("\n", 0, max_length)
        if split_at == -1:
            # Try a space
            split_at = remaining.rfind(" ", 0, max_length)
        if split_at == -1:
            split_at = max_length

        chunks.append(remaining[:split_at])
        remaining = remaining[split_at:].lstrip()

    return chunks
