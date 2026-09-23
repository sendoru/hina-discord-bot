import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from hina_bot.ai.vision import CURRENT_VISUAL_INPUTS
from hina_bot.tooling.eval_runner import (
    attach_routing_results,
    case_turns,
    case_visuals,
    eval_settings,
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
            {"id": "visual", "input": "히나야", "expected": "이미지를 자기 자신으로 오인하지 않는다.",
             "mode": "server", "visuals": [{
                 "fixture": "evals/fixtures/visual-not-hina.png.b64",
                 "mime_type": "image/png",
                 "name": "visual-not-hina.png",
             }]},
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
        self.assertEqual(cases[3]["visuals"][0]["mime_type"], "image/png")
        self.assertEqual(case_visuals(cases[3])[0].context_kind, "current_message")

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
            path.write_text(json.dumps(
                {"id": "bad-visual", "input": "a", "expected": "x",
                 "visuals": [{"fixture": "../secret.png.b64", "mime_type": "image/png"}]})
                + "\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                read_cases(path)
            path.write_text(json.dumps(
                {"id": "multi-visual", "turns": ["a", "b"], "expected": "x",
                 "visuals": [{"fixture": "evals/fixtures/visual-not-hina.png.b64",
                              "mime_type": "image/png"}]}) + "\n", encoding="utf-8")
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

    def test_eval_settings_honors_live_eval_controls(self):
        args = SimpleNamespace(
            provider="gemini",
            model="gemini-3.5-flash-lite",
            usage_log="",
            gemini_thinking_level="minimal",
            routing_mode="fixed",
        )
        with patch.dict(
            "os.environ",
            {
                "GEMINI_API_KEY": "test-key",
                "CHAT_WEB_SEARCH": "false",
                "COMMUNITY_LORE": "true",
                "MAX_OUTPUT_TOKENS": "1000",
            },
            clear=False,
        ):
            settings = eval_settings(args)

        self.assertEqual(settings.provider, "gemini")
        self.assertEqual(settings.gemini_thinking_level, "minimal")
        self.assertFalse(settings.chat_web_search)

    def test_eval_settings_can_reproduce_adaptive_runtime_routing(self):
        args = SimpleNamespace(
            provider="gemini",
            model=None,
            usage_log="eval-usage.jsonl",
            gemini_thinking_level=None,
            gemini_fast_thinking_level="minimal",
            gemini_smart_thinking_level="medium",
            routing_mode="adaptive",
            fast_model="gemini-fast",
            smart_model="gemini-smart",
            smart_threshold=1.75,
            routing_classifier_mode="active",
            routing_classifier_provider="gemini",
            routing_classifier_model="gemini-classifier",
        )
        with patch.dict(
            "os.environ",
            {
                "GEMINI_API_KEY": "test-key",
                "CHAT_WEB_SEARCH": "false",
                "COMMUNITY_LORE": "true",
            },
            clear=False,
        ):
            settings = eval_settings(args)

        self.assertEqual(settings.model_routing_mode, "adaptive")
        self.assertEqual(settings.fast_model, "gemini-fast")
        self.assertEqual(settings.smart_model, "gemini-smart")
        self.assertEqual(settings.model_routing_smart_threshold, 1.75)
        self.assertEqual(settings.routing_classifier_mode, "active")
        self.assertEqual(settings.routing_classifier_model, "gemini-classifier")
        self.assertEqual(settings.gemini_fast_thinking_level, "minimal")
        self.assertEqual(settings.gemini_smart_thinking_level, "medium")

    def test_eval_settings_rejects_invalid_web_search_flag(self):
        args = SimpleNamespace(
            provider="gemini",
            model="gemini-3.5-flash-lite",
            usage_log="",
            gemini_thinking_level=None,
            routing_mode="fixed",
        )
        with patch.dict(
            "os.environ",
            {"GEMINI_API_KEY": "test-key", "CHAT_WEB_SEARCH": "sometimes"},
            clear=False,
        ), self.assertRaises(ValueError):
            eval_settings(args)

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
        self.assertEqual(len(result["turn_ids"]), 1)
        self.assertTrue(result["turn_ids"][0])
        self.assertEqual(result["routing"], [])

    async def test_run_case_exposes_sanitized_visual_fixture_to_llm(self):
        class FakeLLM:
            settings = SimpleNamespace(history_turns=12, provider="test", model="fake")

            def __init__(self):
                self.visuals = []

            async def answer(self, store, scope, name, content, **kwargs):
                self.visuals.append(CURRENT_VISUAL_INPUTS.get())
                return "ok"

        case = {
            "id": "visual",
            "mode": "server",
            "speaker": "A",
            "input": "히나야",
            "visuals": [{
                "fixture": "evals/fixtures/visual-not-hina.png.b64",
                "mime_type": "image/png",
                "name": "visual-not-hina.png",
            }],
            "expected": "이미지를 자기 자신으로 오인하지 않는다.",
        }
        llm = FakeLLM()

        result = await run_case(llm, case)

        self.assertEqual(result["error"], "")
        self.assertEqual(len(llm.visuals), 1)
        self.assertEqual(len(llm.visuals[0]), 1)
        self.assertEqual(llm.visuals[0][0].mime_type, "image/png")
        self.assertEqual(llm.visuals[0][0].source, "attachment")
        self.assertEqual(llm.visuals[0][0].context_kind, "current_message")
        self.assertEqual(CURRENT_VISUAL_INPUTS.get(), ())
        self.assertEqual(result["visuals"], case["visuals"])


def test_attach_routing_results_reports_actual_answer_model_and_tier(tmp_path):
    usage = tmp_path / "usage.jsonl"
    usage.write_text(
        json.dumps({
            "turn_id": "turn-a",
            "operation": "model_route_classify",
            "model": "classifier",
            "model_tier": "fast",
        }) + "\n" +
        json.dumps({
            "turn_id": "turn-a",
            "operation": "answer",
            "provider": "gemini",
            "model": "gemini-smart",
            "model_tier": "smart",
            "model_route_baseline_tier": "fast",
            "model_route_decision_source": "semantic",
            "semantic_route_status": "completed",
            "semantic_route_level": "medium",
            "model_route_margin": 0.5,
            "requested_thinking_level": "medium",
        }) + "\n",
        encoding="utf-8",
    )
    results = [{"turn_ids": ["turn-a"], "routing": []}]

    attach_routing_results(results, str(usage))

    assert results[0]["routing"] == [{
        "turn_id": "turn-a",
        "provider": "gemini",
        "model": "gemini-smart",
        "model_tier": "smart",
        "model_route_baseline_tier": "fast",
        "model_route_decision_source": "semantic",
        "semantic_route_status": "completed",
        "semantic_route_level": "medium",
        "model_route_margin": 0.5,
        "requested_thinking_level": "medium",
    }]


def test_production_quality_cases_are_sanitized_and_cover_live_failure_shapes():
    cases = read_cases(Path("evals/production_quality_cases.jsonl"))
    ids = {case["id"] for case in cases}
    assert ids == {
        "production_temporal_continuity_same_session",
        "production_profane_game_complaint_not_policed",
        "production_unknown_meme_sequence_not_scolded",
        "production_multilingual_partial_understanding",
        "production_world_followup_answers_hypothetical",
        "production_multi_bot_addressee_not_self",
        "production_ambiguous_deictic_does_not_invent",
        "production_visual_address_not_self_attribution",
    }
    raw = Path("evals/production_quality_cases.jsonl").read_text(encoding="utf-8")
    assert "478976784881287178" not in raw
    assert "135336096480493568" not in raw
    assert "1548019497628082196" not in raw


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
    assert "avoid_repetitive_reaction_vocabulary" in ids


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
