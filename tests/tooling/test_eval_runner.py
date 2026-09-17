import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from hina_bot.tooling.eval_runner import (
    case_turns,
    read_cases,
    response_validation_errors,
    run_case,
    scope_for,
)


class EvalRunnerTests(unittest.TestCase):
    def test_reads_single_multi_turn_and_channel_context_cases(self):
        rows = [
            {"id": "single", "input": "안녕", "expected": "짧게 인사한다."},
            {"id": "multi", "turns": ["안녕", "오늘 뭐 했어?"],
             "expected": "맥락을 유지한다.", "mode": "special_dm"},
            {"id": "channel", "input": "배고파", "expected": "현재 화자에게 답한다.",
             "mode": "server", "channel_context": [
                 {"user_id": "99", "name": "A", "role": "user", "content": "아까 한 말"},
             ], "validators": ["python_syntax"]},
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
        self.assertEqual(cases[2]["validators"], ["python_syntax"])

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
                {"id": "bad-validator", "input": "a", "expected": "x",
                 "validators": ["execute_python"]}) + "\n", encoding="utf-8")
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

    def test_python_response_validators_parse_without_execution(self):
        valid = "설명\n```python\ndef solve():\n    return 1\n```"
        invalid = "```python\ndef solve():\nreturn 1\n```"
        self.assertEqual(
            response_validation_errors(valid, ["python_fenced_code", "python_syntax"]),
            [],
        )
        errors = response_validation_errors(
            invalid,
            ["python_fenced_code", "python_syntax"],
        )
        self.assertEqual(len(errors), 1)
        self.assertIn("구문 오류", errors[0])
        self.assertEqual(
            response_validation_errors("print(1)", ["python_fenced_code", "python_syntax"]),
            ["python 코드 블록이 없습니다."],
        )


class EvalRunnerAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_run_case_passes_channel_context_to_llm(self):
        class FakeLLM:
            settings = SimpleNamespace(history_turns=12, provider="test", model="fake")

            def __init__(self):
                self.contexts = []

            async def answer(self, store, scope, name, content, **kwargs):
                self.contexts.append(kwargs["channel_context"])
                return "ok"

        context = [{"user_id": "99", "name": "A", "role": "user", "content": "직전 발언"}]
        case = {
            "id": "channel",
            "mode": "server",
            "speaker": "B",
            "input": "배고파",
            "channel_context": context,
            "expected": "현재 화자에게 답한다.",
        }
        llm = FakeLLM()

        result = await run_case(llm, case)

        self.assertEqual(llm.contexts, [context])
        self.assertEqual(result["channel_context"], context)
        self.assertEqual(result["responses"], ["ok"])
        self.assertEqual(result["error"], "")
        self.assertEqual(result["validation_errors"], [])


def test_tone_cases_include_valid_speaker_switches():
    cases = read_cases(Path("evals/tone_cases.jsonl"))
    case = next(row for row in cases if row["id"] == "speaker_switch_does_not_transfer_anger")
    assert len(case_turns(case)) == 4


def test_tone_cases_include_relationship_honorific_perspective():
    cases = read_cases(Path("evals/tone_cases.jsonl"))
    case = next(row for row in cases if row["id"] == "relationship_honorific_perspective")
    assert "호시노 선배" in case["expected"]
    assert "유메 선배" in case["expected"]


def test_tone_cases_include_conversational_baseline_regressions():
    cases = read_cases(Path("evals/tone_cases.jsonl"))
    ids = {row["id"] for row in cases}
    assert "informational_question_not_rejected_as_off_topic" in ids
    assert "single_silly_message_not_scolded" in ids


def test_invalid_speaker_turns_are_rejected(tmp_path):
    import pytest

    path = tmp_path / "cases.jsonl"
    for mode, turn in [
        ("dm", {"input": "안녕", "speaker": "A", "user_id": 1}),
        ("server", {"input": "안녕", "speaker": "A", "user_id": True}),
        ("server", {"input": "안녕", "speaker": "A", "user_id": -1}),
        ("server", {"input": "안녕", "speaker": "A"}),
    ]:
        path.write_text(json.dumps({"id": "bad", "mode": mode, "turns": [turn],
                                    "expected": "test"}), encoding="utf-8")
        with pytest.raises(ValueError):
            read_cases(path)


class SpeakerSwitchTests(unittest.IsolatedAsyncioTestCase):
    async def test_generated_channel_context_follows_speakers_and_keeps_memory_isolated(self):
        class FakeLLM:
            settings = SimpleNamespace(history_turns=12, provider="test", model="fake")

            def __init__(self):
                self.calls = []

            async def answer(self, store, scope, name, content, **kwargs):
                self.calls.append((scope.user_id, name, kwargs["channel_context"],
                                   [row["content"] for row in store.history(scope)]))
                return f"reply {name}"

        case = {"id": "switch", "mode": "server", "expected": "화자 격리", "turns": [
            {"user_id": 101, "speaker": "A", "input": "장난"},
            {"user_id": 102, "speaker": "B", "input": "안녕"},
            {"user_id": 101, "speaker": "A", "input": "질문"},
        ]}
        llm = FakeLLM()
        result = await run_case(llm, case)
        self.assertEqual(result["error"], "")
        self.assertEqual(llm.calls[0][2], [])
        self.assertEqual(llm.calls[1][0:2], (102, "B"))
        self.assertEqual(llm.calls[1][3], [])
        self.assertEqual(llm.calls[1][2][0]["user_id"], "101")
        self.assertEqual(llm.calls[1][2][1]["reply_target_user_id"], "101")
        self.assertEqual(llm.calls[1][2][1]["content"], "reply A")
        self.assertEqual(llm.calls[2][3], ["장난"])
        self.assertEqual(result["speakers"][1], {"user_id": 102, "speaker": "B"})
        self.assertNotIn("channel_context", case)


if __name__ == "__main__":
    unittest.main()
