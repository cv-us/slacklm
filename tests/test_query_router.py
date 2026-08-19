import pytest
from unittest.mock import AsyncMock, MagicMock

from app.config import Config, NotebookMapping, ClaudeFallbackConfig
from app.notebooklm_client import Answer
from app.query_router import QueryRouter, parse_message, resolve_notebook


def make_config(**overrides):
    defaults = dict(
        slack_bot_token="xoxb-test",
        slack_app_token="xapp-test",
        anthropic_api_key="sk-test",
        channel_mappings={
            "C001": NotebookMapping(notebook_id="nb-001", name="Engineering Docs"),
            "C002": NotebookMapping(notebook_id="nb-002", name="HR Policies"),
        },
        default_notebook=NotebookMapping(notebook_id="nb-default", name="Default"),
        claude_fallback=ClaudeFallbackConfig(enabled=True, model="claude-sonnet-4-6"),
    )
    defaults.update(overrides)
    return Config(**defaults)


class TestParseMessage:
    def test_plain_question(self):
        config = make_config()
        override, question = parse_message("what is our PTO policy?", config)
        assert override is None
        assert question == "what is our PTO policy?"

    def test_override_syntax(self):
        config = make_config()
        override, question = parse_message("in hr-policies: what is PTO?", config)
        assert override == "hr-policies"
        assert question == "what is PTO?"

    def test_override_case_insensitive(self):
        config = make_config()
        override, question = parse_message("In HR-Policies: question", config)
        assert override == "HR-Policies"
        assert question == "question"

    def test_no_override_with_colon_in_question(self):
        config = make_config()
        override, question = parse_message("what does this mean: something?", config)
        assert override is None
        assert question == "what does this mean: something?"


class TestResolveNotebook:
    def test_channel_mapping(self):
        config = make_config()
        nb = resolve_notebook(config, "C001", None)
        assert nb.notebook_id == "nb-001"

    def test_default_fallback(self):
        config = make_config()
        nb = resolve_notebook(config, "C999", None)
        assert nb.notebook_id == "nb-default"

    def test_override_by_name(self):
        config = make_config()
        nb = resolve_notebook(config, "C001", "hr-policies")
        assert nb.notebook_id == "nb-002"

    def test_unknown_override_falls_back(self):
        config = make_config()
        nb = resolve_notebook(config, "C001", "nonexistent")
        # Falls back to channel mapping
        assert nb.notebook_id == "nb-001"


class TestQueryRouter:
    @pytest.mark.asyncio
    async def test_notebooklm_success(self):
        config = make_config()
        nlm = AsyncMock()
        nlm.ask.return_value = Answer(text="The answer is 42.", engine="notebooklm")

        router = QueryRouter(config, nlm, None)
        answer = await router.query("C001", "what is the answer?")

        assert answer.text == "The answer is 42."
        assert answer.engine == "notebooklm"
        nlm.ask.assert_called_once_with("nb-001", "what is the answer?")

    @pytest.mark.asyncio
    async def test_fallback_to_claude(self):
        config = make_config()
        nlm = AsyncMock()
        nlm.ask.side_effect = Exception("NotebookLM is down")

        claude = AsyncMock()
        claude.ask.return_value = Answer(text="Claude says 42.", engine="claude")

        router = QueryRouter(config, nlm, claude)
        answer = await router.query("C001", "what is the answer?")

        assert answer.text == "Claude says 42."
        assert answer.engine == "claude"
        # NotebookLM should have been retried once before falling back
        assert nlm.ask.call_count == 2

    @pytest.mark.asyncio
    async def test_transient_failure_retries_and_succeeds(self):
        config = make_config()
        nlm = AsyncMock()
        nlm.ask.side_effect = [
            Exception("stream stall: no bytes for 20s"),
            Answer(text="Second try worked.", engine="notebooklm"),
        ]
        claude = AsyncMock()

        router = QueryRouter(config, nlm, claude)
        answer = await router.query("C001", "question")

        assert answer.text == "Second try worked."
        assert answer.engine == "notebooklm"
        assert nlm.ask.call_count == 2
        claude.ask.assert_not_called()

    @pytest.mark.asyncio
    async def test_on_retry_callback_fires_once(self):
        config = make_config()
        nlm = AsyncMock()
        nlm.ask.side_effect = [
            Exception("stall"),
            Answer(text="ok", engine="notebooklm"),
        ]
        on_retry = AsyncMock()

        router = QueryRouter(config, nlm, None)
        answer = await router.query("C001", "question", on_retry=on_retry)

        assert answer.text == "ok"
        on_retry.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_on_retry_not_fired_on_success(self):
        config = make_config()
        nlm = AsyncMock()
        nlm.ask.return_value = Answer(text="first try", engine="notebooklm")
        on_retry = AsyncMock()

        router = QueryRouter(config, nlm, None)
        await router.query("C001", "question", on_retry=on_retry)

        on_retry.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_both_fail(self):
        config = make_config()
        nlm = AsyncMock()
        nlm.ask.side_effect = Exception("NLM down")

        claude = AsyncMock()
        claude.ask.side_effect = Exception("Claude down")

        router = QueryRouter(config, nlm, claude)
        answer = await router.query("C001", "question")

        assert answer.engine == "error"
        assert "unavailable" in answer.text.lower()

    @pytest.mark.asyncio
    async def test_no_notebook_configured(self):
        config = make_config(channel_mappings={}, default_notebook=None)
        nlm = AsyncMock()
        router = QueryRouter(config, nlm, None)
        answer = await router.query("C999", "question")

        assert answer.engine == "system"
        assert "no notebook" in answer.text.lower()

    @pytest.mark.asyncio
    async def test_override_routes_correctly(self):
        config = make_config()
        nlm = AsyncMock()
        nlm.ask.return_value = Answer(text="HR answer.", engine="notebooklm")

        router = QueryRouter(config, nlm, None)
        answer = await router.query("C001", "in hr-policies: what is PTO?")

        nlm.ask.assert_called_once_with("nb-002", "what is PTO?")
