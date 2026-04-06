from app.formatter import format_answer, format_sources_list, format_help, _split_text
from app.notebooklm_client import Answer, Source


class TestFormatAnswer:
    def test_basic_answer(self):
        answer = Answer(text="The policy says X.", engine="notebooklm")
        blocks = format_answer(answer)
        assert any(b["type"] == "section" for b in blocks)
        text_blocks = [b for b in blocks if b["type"] == "section"]
        assert "The policy says X." in text_blocks[0]["text"]["text"]

    def test_engine_indicator_notebooklm(self):
        answer = Answer(text="Answer.", engine="notebooklm")
        blocks = format_answer(answer)
        context_blocks = [b for b in blocks if b["type"] == "context"]
        assert any("NotebookLM" in str(b) for b in context_blocks)

    def test_engine_indicator_claude(self):
        answer = Answer(text="Answer.", engine="claude")
        blocks = format_answer(answer)
        context_blocks = [b for b in blocks if b["type"] == "context"]
        assert any("Claude" in str(b) for b in context_blocks)

    def test_with_citations(self):
        answer = Answer(
            text="Answer text.",
            citations=["Page 5: relevant quote"],
            engine="notebooklm",
        )
        blocks = format_answer(answer)
        assert any(b.get("type") == "divider" for b in blocks)
        assert any("Citations" in str(b) for b in blocks)

    def test_with_sources(self):
        answer = Answer(
            text="Answer.",
            sources=[Source(source_id="1", title="HR Policy.pdf")],
            engine="notebooklm",
        )
        blocks = format_answer(answer)
        assert any("HR Policy.pdf" in str(b) for b in blocks)

    def test_long_answer_splits(self):
        long_text = "A" * 5000
        answer = Answer(text=long_text, engine="notebooklm")
        blocks = format_answer(answer)
        section_blocks = [b for b in blocks if b["type"] == "section"]
        assert len(section_blocks) >= 2


class TestFormatSourcesList:
    def test_empty_sources(self):
        blocks = format_sources_list([])
        assert "No sources found" in str(blocks)

    def test_with_sources(self):
        sources = [
            {"title": "Doc A", "source_id": "1"},
            {"title": "Doc B", "source_id": "2"},
        ]
        blocks = format_sources_list(sources)
        assert "Doc A" in str(blocks)
        assert "Doc B" in str(blocks)


class TestFormatHelp:
    def test_help_contains_usage(self):
        blocks = format_help()
        text = str(blocks)
        assert "SlackLM" in text
        assert "sources" in text
        assert "help" in text


class TestSplitText:
    def test_short_text(self):
        assert _split_text("hello", 100) == ["hello"]

    def test_splits_at_paragraph(self):
        text = "A" * 50 + "\n\n" + "B" * 50
        chunks = _split_text(text, 60)
        assert len(chunks) == 2

    def test_splits_at_newline(self):
        text = "A" * 50 + "\n" + "B" * 50
        chunks = _split_text(text, 60)
        assert len(chunks) == 2

    def test_splits_at_space(self):
        text = "word " * 20
        chunks = _split_text(text, 30)
        assert len(chunks) >= 2
        for chunk in chunks:
            assert len(chunk) <= 30
