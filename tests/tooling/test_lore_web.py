import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch

from hina_bot.core.lore import read_jsonl, write_jsonl
from hina_bot.tooling import lore_cli, lore_pipeline
from hina_bot.tooling.lore_web import _extract_web_sources, verify_candidate


def candidate(identifier: str = "canon.hina.test", **overrides) -> dict:
    row = {
        "id": identifier,
        "source_id": "source-001",
        "lane": "canon",
        "fact_type": "fact_direct",
        "summary": "히나는 시험 설정을 알고 있다.",
        "keywords": ["시험 설정"],
        "subjects": ["히나"],
        "knowledge": "self",
        "confidence": "candidate",
        "status": "candidate",
        "kr_release": "pending",
        "timeline": "테스트 장면",
        "source": {
            "type": "curated_research",
            "title": "정제 자료",
            "url": "",
            "locator": "테스트",
        },
        "evidence": "테스트 근거",
        "uncertainty": "",
        "kr_release_evidence": "",
    }
    row.update(overrides)
    return row


class FakeResponse:
    def __init__(self, parsed: dict, sources: list[dict], *, status: str = "completed"):
        self.status = status
        self.output_text = json.dumps(parsed, ensure_ascii=False)
        self._payload = {
            "output": [
                {
                    "type": "web_search_call",
                    "action": {"sources": sources},
                },
                {"type": "message"},
            ]
        }

    def model_dump(self, mode="json"):
        return self._payload


class LoreWebTests(unittest.TestCase):
    def test_extract_web_sources_deduplicates_urls(self):
        response = FakeResponse(
            {
                "status": "corroborated",
                "source_quality": "primary",
                "note": "ok",
                "kr_release": "confirmed",
                "kr_release_note": "ok",
                "relied_urls": [],
            },
            [
                {"url": "https://example.com/a", "title": "A"},
                {"url": "https://example.com/a", "title": "A duplicate"},
                {"url": "https://example.com/b", "title": "B"},
            ],
        )
        self.assertEqual(
            [source["url"] for source in _extract_web_sources(response)],
            ["https://example.com/a", "https://example.com/b"],
        )

    def test_verify_candidate_keeps_supported_kr_release(self):
        parsed = {
            "status": "corroborated",
            "source_quality": "primary",
            "note": "한국 공식 자료가 설정을 뒷받침한다.",
            "kr_release": "confirmed",
            "kr_release_note": "한국 공지에서 공개를 확인했다.",
            "relied_urls": [
                "https://forum.nexon.com/bluearchive/board_view?thread=1",
            ],
        }
        response = FakeResponse(
            parsed,
            [{
                "url": "https://forum.nexon.com/bluearchive/board_view?thread=1",
                "title": "Blue Archive 공지",
            }],
        )
        client = NS(responses=NS(create=lambda **kwargs: response))
        result = verify_candidate(client, candidate(), model="gpt-5.4-mini")
        self.assertEqual(result["status"], "corroborated")
        self.assertEqual(result["kr_release"], "confirmed")
        self.assertEqual(result["search_calls"], 1)
        self.assertEqual(len(result["sources"]), 1)

    def test_verify_candidate_downgrades_unproven_kr_release(self):
        parsed = {
            "status": "corroborated",
            "source_quality": "secondary",
            "note": "2차 자료가 설정을 뒷받침한다.",
            "kr_release": "confirmed",
            "kr_release_note": "출시되었다고 적혀 있다.",
            "relied_urls": ["https://example-wiki.test/hina"],
        }
        response = FakeResponse(
            parsed,
            [{"url": "https://example-wiki.test/hina", "title": "Wiki"}],
        )
        client = NS(responses=NS(create=lambda **kwargs: response))
        result = verify_candidate(client, candidate(), model="gpt-5.4-mini")
        self.assertEqual(result["status"], "corroborated")
        self.assertEqual(result["kr_release"], "not_found")
        self.assertIn("confirmed를 보류", result["kr_release_note"])

    def test_verify_web_writes_queue_and_skips_checked_without_force(self):
        row = candidate()
        parsed = {
            "status": "corroborated",
            "source_quality": "primary",
            "note": "확인됨",
            "kr_release": "confirmed",
            "kr_release_note": "한국 공식 공지 확인",
            "relied_urls": ["https://forum.nexon.com/bluearchive/board_view?thread=1"],
        }
        response = FakeResponse(
            parsed,
            [{
                "url": "https://forum.nexon.com/bluearchive/board_view?thread=1",
                "title": "공지",
            }],
        )
        client = NS(responses=NS(create=lambda **kwargs: response))

        with tempfile.TemporaryDirectory() as directory:
            queue_path = Path(directory) / "review.jsonl"
            write_jsonl(queue_path, [row])
            args = NS(
                id=row["id"],
                source_type=None,
                id_prefix=None,
                title=None,
                fact_type=None,
                force=False,
                limit=20,
                dry_run=False,
                model="gpt-5.4-mini",
            )
            with (
                patch.object(lore_pipeline, "QUEUE_PATH", queue_path),
                patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}),
                patch("openai.OpenAI", return_value=client),
            ):
                lore_cli.verify_web(args)
                saved = read_jsonl(queue_path)[0]
                self.assertEqual(saved["verification"]["status"], "corroborated")
                self.assertIn("web:", saved["kr_release_evidence"])
                with self.assertRaises(SystemExit):
                    lore_cli.verify_web(args)

    def test_verify_web_requires_narrowing_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            queue_path = Path(directory) / "review.jsonl"
            write_jsonl(queue_path, [candidate()])
            args = NS(
                id=None,
                source_type=None,
                id_prefix=None,
                title=None,
                fact_type=None,
                force=False,
                limit=20,
                dry_run=True,
                model="gpt-5.4-mini",
            )
            with (
                patch.object(lore_pipeline, "QUEUE_PATH", queue_path),
                patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}),
                self.assertRaises(SystemExit),
            ):
                lore_cli.verify_web(args)


if __name__ == "__main__":
    unittest.main()
