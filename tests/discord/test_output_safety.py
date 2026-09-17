import unittest

from hina_bot.discord.output_safety import DISCORD_MENTION, neutralize_mentions


class OutputSafetyTests(unittest.TestCase):
    def test_broadcast_and_role_mentions_are_always_neutralized(self):
        unsafe = "@everyone @here <@&789>"
        result = neutralize_mentions(unsafe, allow_user_mentions=True)
        self.assertIsNone(DISCORD_MENTION.search(result))
        self.assertEqual(result, "＠everyone ＠here <＠&789>")

    def test_user_mentions_are_preserved_when_enabled(self):
        text = "<@123> <@!456> 안녕"
        self.assertEqual(neutralize_mentions(text, allow_user_mentions=True), text)

    def test_user_mentions_are_neutralized_when_disabled(self):
        text = "<@123> <@!456> 안녕"
        self.assertEqual(
            neutralize_mentions(text, allow_user_mentions=False),
            "<＠123> <＠!456> 안녕",
        )

    def test_normal_text_and_custom_emoji_are_unchanged(self):
        text = "안녕 @user <:hina_happy:1234> 이메일 user@example.com"
        self.assertEqual(neutralize_mentions(text, allow_user_mentions=False), text)

    def test_case_variants_are_neutralized(self):
        self.assertEqual(
            neutralize_mentions("@Everyone @HERE", allow_user_mentions=True),
            "＠Everyone ＠HERE",
        )
