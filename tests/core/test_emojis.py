import unittest
from types import SimpleNamespace as NS

from hina_bot.core.emojis import available_emojis, render_emojis
from hina_bot.core.routing import chunks


class Emoji:
    def __init__(self, name, id, animated=False, usable=True):
        self.name, self.id, self.animated, self.usable = name, id, animated, usable

    def is_usable(self):
        return self.usable

    def __str__(self):
        return f'<{"a" if self.animated else ""}:{self.name}:{self.id}>'


class EmojiTests(unittest.TestCase):
    def test_catalog_usable_only_and_bounded(self):
        guild = NS(unavailable=False, me=NS(), emojis=[
            Emoji("other", 1), Emoji("hina_happy", 2, animated=True),
            Emoji("hina_locked", 3, usable=False)])
        catalog = available_emojis(guild, limit=1)
        self.assertEqual(catalog, [{"name": "hina_happy", "markup": "<a:hina_happy:2>", "id": "2"}])
        self.assertEqual(available_emojis(None), [])

    def test_render_corrects_animation_and_rejects_unknown_ids(self):
        catalog = [{"name": "hina_happy", "id": "2", "markup": "<a:hina_happy:2>"}]
        self.assertEqual(render_emojis("응 <:fake:2> <:other:3>", catalog), "응 <a:hina_happy:2>")
        self.assertEqual(render_emojis(":hina_happy:", catalog), "<a:hina_happy:2>")
        self.assertEqual(render_emojis("<a:hina_happy:2>", []), "")

    def test_limit_and_unicode_preserved(self):
        catalog = [{"name": "ok", "id": "2", "markup": "<:ok:2>"}]
        self.assertEqual(render_emojis("😀 :ok: :ok: :ok:", catalog), "😀 <:ok:2> <:ok:2>")

    def test_split_preserves_markup(self):
        text = "x" * 1898 + "<a:hina_happy:12345>" + "hello"
        parts = list(chunks(text))
        self.assertEqual("".join(parts), text)
        self.assertTrue(parts[1].startswith("<a:hina_happy:12345>"))
        self.assertTrue(all(len(p) <= 1900 for p in parts))


def test_catalog_preference_is_configurable(monkeypatch):
    monkeypatch.setenv("CHARACTER_EMOJI_PREFIXES", "aris")
    guild = NS(unavailable=False, me=NS(), emojis=[
        Emoji("hina_happy", 1), Emoji("aris_happy", 2), Emoji("other", 3),
    ])

    catalog = available_emojis(guild, limit=1)

    assert catalog == [{"name": "aris_happy", "markup": "<:aris_happy:2>", "id": "2"}]
