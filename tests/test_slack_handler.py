from app.slack_handler import strip_bot_mention


class TestStripBotMention:
    def test_removes_mention(self):
        assert strip_bot_mention("<@U12345> what is PTO?") == "what is PTO?"

    def test_no_mention(self):
        assert strip_bot_mention("what is PTO?") == "what is PTO?"

    def test_mention_only(self):
        assert strip_bot_mention("<@U12345>") == ""

    def test_mention_with_extra_spaces(self):
        assert strip_bot_mention("<@U12345>   hello") == "hello"
