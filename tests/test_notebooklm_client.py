from app.notebooklm_client import (
    make_overview,
    slice_window,
    SECTION_READ_MAX_CHARS,
)


class TestMakeOverview:
    def test_small_document_fits_in_head(self):
        content = "short document"
        ov = make_overview(content, head_chars=100, tail_chars=50)
        assert ov["char_count"] == len(content)
        assert ov["head"] == content
        assert ov["tail"] == ""

    def test_large_document_splits_head_and_tail(self):
        content = "A" * 500 + "B" * 500 + "C" * 500
        ov = make_overview(content, head_chars=400, tail_chars=300)
        assert ov["char_count"] == 1500
        assert ov["head"] == "A" * 400
        assert ov["tail"] == "C" * 300

    def test_boundary_exact_fit(self):
        content = "X" * 150
        ov = make_overview(content, head_chars=100, tail_chars=50)
        assert ov["head"] == content
        assert ov["tail"] == ""


class TestSliceWindow:
    def test_basic_window(self):
        content = "0123456789" * 10  # 100 chars
        w = slice_window(content, 10, 20)
        assert w["start_char"] == 10
        assert w["end_char"] == 30
        assert w["content"] == content[10:30]
        assert w["char_count"] == 100

    def test_window_clamped_to_end(self):
        content = "abc" * 10  # 30 chars
        w = slice_window(content, 25, 50)
        assert w["end_char"] == 30
        assert w["content"] == content[25:30]

    def test_negative_start_clamped(self):
        content = "hello world"
        w = slice_window(content, -5, 5)
        assert w["start_char"] == 0
        assert w["content"] == "hello"

    def test_start_beyond_end_clamped(self):
        content = "hello"
        w = slice_window(content, 100, 10)
        assert w["start_char"] == 4
        assert w["content"] == "o"

    def test_length_capped_at_max(self):
        content = "z" * (SECTION_READ_MAX_CHARS + 10_000)
        w = slice_window(content, 0, SECTION_READ_MAX_CHARS + 5_000)
        assert len(w["content"]) == SECTION_READ_MAX_CHARS

    def test_empty_document(self):
        w = slice_window("", 0, 100)
        assert w["char_count"] == 0
        assert w["content"] == ""
