import json
import tempfile
import unittest
from pathlib import Path

from hina_bot.eval_runner import case_turns, read_cases, scope_for


class EvalRunnerTests(unittest.TestCase):
    def test_reads_single_multi_turn_and_channel_context_cases(self):
        rows = [
            {"id": "single", "input": "안녕", "expected": "짧게 인사한다."},
            {"id": "multi", "turns": ["안녕", "오늘 뭐 했어?"],
             "expected": "맥락을 유지한다.", "mode": "special_dm"},
            {"id": "channel", "input": "배고파", "expected": "현재 화자에게 답한다.",
             "mode": "server", "channel_context": [
                 {"user_id": "99", "name": "A", "role": "user", "content": "아까 한 말"},
             ]},
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cases.jsonl"
            path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
                            encoding="utf-8")
            cases = read_cases(path)
        self.assertEqual(case_turns(cases[0]), ["안녕"])
        self.assertEqual(case_turns(cases[1]), ["안녕", "오늘 뭐 했어?"])
        self.assertEqual(cases[1]["mode"], "special_dm")
        self.assertEqual(cases[2]["channel_context"][0]["name"], "A")

    def test_rejects_duplicate_ids_invalid_mode_and_bad_channel_context(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cases.jsonl"
            path.write_text(
                json.dumps({"id": "same", "input": "a", "expected": "x"}) + "\n" +
                json.dumps({"id": "same", "input": "b", "expected": "y"}) + "\n",
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                read_cases(path)
            path.write_text(json.dumps(
                {"id": "bad", "input": "a", "expected": "x", "mode": "invalid"}) + "\n",
                encoding="utf-8")
            with self.assertRaises(ValueError):
                read_cases(path)
            path.write_text(json.dumps(
                {"id": "bad-context", "input": "a", "expected": "x",
                 "channel_context": [{"name": "A"}]}) + "\n",
                encoding="utf-8")
            with self.assertRaises(ValueError):
                read_cases(path)

    def test_modes_use_isolated_scopes(self):
        normal = scope_for("dm")
        special = scope_for("special_dm")
        server = scope_for("server")
        self.assertIsNone(normal.guild_id)
        self.assertIsNone(special.guild_id)
        self.assertNotEqual(normal.user_id, special.user_id)
        self.assertIsNotNone(server.guild_id)


if __name__ == "__main__":
    unittest.main()
