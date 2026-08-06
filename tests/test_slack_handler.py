from app.slack_handler import strip_bot_mention, ThreadTracker


class TestStripBotMention:
    def test_removes_mention(self):
        assert strip_bot_mention("<@U12345> what is PTO?") == "what is PTO?"

    def test_no_mention(self):
        assert strip_bot_mention("what is PTO?") == "what is PTO?"

    def test_mention_only(self):
        assert strip_bot_mention("<@U12345>") == ""

    def test_mention_with_extra_spaces(self):
        assert strip_bot_mention("<@U12345>   hello") == "hello"


class TestThreadTracker:
    def test_track_and_check(self):
        tracker = ThreadTracker()
        tracker.track("C001", "123.456")
        assert tracker.is_tracked("C001", "123.456")

    def test_untracked_thread(self):
        tracker = ThreadTracker()
        assert not tracker.is_tracked("C001", "123.456")

    def test_different_channel_same_ts(self):
        tracker = ThreadTracker()
        tracker.track("C001", "123.456")
        assert not tracker.is_tracked("C002", "123.456")

    def test_eviction_beyond_max_size(self):
        tracker = ThreadTracker(max_size=3)
        for i in range(5):
            tracker.track("C001", f"ts-{i}")
        assert not tracker.is_tracked("C001", "ts-0")
        assert not tracker.is_tracked("C001", "ts-1")
        assert tracker.is_tracked("C001", "ts-2")
        assert tracker.is_tracked("C001", "ts-4")

    def test_retracking_refreshes_position(self):
        tracker = ThreadTracker(max_size=2)
        tracker.track("C001", "old")
        tracker.track("C001", "new")
        tracker.track("C001", "old")  # refresh: "old" is now most recent
        tracker.track("C001", "newest")  # evicts "new"
        assert tracker.is_tracked("C001", "old")
        assert not tracker.is_tracked("C001", "new")
