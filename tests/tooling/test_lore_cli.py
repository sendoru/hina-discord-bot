import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch

from hina_bot.core.lore import read_jsonl, validate_record, write_jsonl
from hina_bot.tooling import lore_cli, lore_pipeline


def candidate(identifier: str, *, fact_type: str = "fact_direct",
              source_type: str = "curated_research", status: str = "candidate",
              title: str = "정제 자료", lane: str = "canon", **overrides) -> dict:
    row = {
        "id": identifier,
        "source_id": f"source-{identifier}",
        "lane": lane,
        "fact_type": fact_type,
        "summary": "히나는 검수용 사실을 알고 있다.",
        "keywords": ["검수"],
        "subjects": ["히나"],
        "knowledge": "self",
        "confidence": "candidate",
        "status": status,
        "kr_release": "pending" if lane == "canon" else "not_applicable",
        "timeline": "테스트 시점",
        "source": {"type": source_type, "title": title, "url": "", "locator": "테스트"},
        "evidence": "테스트 근거",
        "uncertainty": "",
        "kr_release_evidence": "한국 서버 확인",
    }
    if lane == "community_meme":
        row["reaction"] = "짧게 반응한다."
    row.update(overrides)
    return row


def accepted(identifier: str) -> dict:
    row = candidate(identifier, source_type="official_game")
    row["status"] = "accepted"
    row["confidence"] = "verified"
    row["kr_release"] = "confirmed"
    return {
        key: value for key, value in row.items()
        if key not in {"source_id", "evidence", "uncertainty", "kr_release_evidence"}
    }


def args(**overrides):
    values = {
        "source_type": None,
        "id_prefix": "canon.bulk.",
        "title": None,
        "fact_type": None,
        "confirm_kr_release": True,
        "confidence": None,
        "dry_run": False,
        "yes": True,
    }
    values.update(overrides)
    return NS(**values)


class LoreBulkApprovalTests(unittest.TestCase):
    def test_dry_run_does_not_change_files(self):
        queue = [
            candidate("canon.bulk.direct"),
            candidate("canon.bulk.inference", fact_type="inference"),
        ]
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            queue_path = base / "review.jsonl"
            runtime_path = base / "lore.jsonl"
            write_jsonl(queue_path, queue)
            with (patch.object(lore_pipeline, "QUEUE_PATH", queue_path),
                  patch.object(lore_pipeline, "RUNTIME_PATH", runtime_path)):
                lore_cli.approve_all(args(dry_run=True, yes=False))
                self.assertEqual(read_jsonl(queue_path), queue)
                self.assertEqual(read_jsonl(runtime_path), [])

    def test_apply_is_filtered_and_skips_conflicts_duplicates_and_suppressed(self):
        existing = accepted("canon.bulk.duplicate")
        queue = [
            candidate("canon.bulk.official", source_type="official_game"),
            candidate("canon.bulk.inference", fact_type="inference", source_type="official_game"),
            candidate("canon.bulk.curated"),
            candidate("canon.bulk.duplicate"),
            candidate("canon.bulk.suppressed", fact_type="fandom", status="suppressed"),
            candidate(
                "canon.bulk.conflict",
                verification={"status": "conflict"},
            ),
            candidate("canon.other.outside"),
        ]
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            queue_path = base / "review.jsonl"
            runtime_path = base / "lore.jsonl"
            write_jsonl(queue_path, queue)
            write_jsonl(runtime_path, [existing])
            with (patch.object(lore_pipeline, "QUEUE_PATH", queue_path),
                  patch.object(lore_pipeline, "RUNTIME_PATH", runtime_path)):
                lore_cli.approve_all(args())
                runtime = read_jsonl(runtime_path)
                by_id = {row["id"]: row for row in runtime}
                self.assertEqual(len(runtime), 4)
                self.assertEqual(by_id["canon.bulk.official"]["confidence"], "verified")
                self.assertEqual(by_id["canon.bulk.inference"]["confidence"], "crosschecked")
                self.assertEqual(by_id["canon.bulk.curated"]["confidence"], "crosschecked")
                for row in runtime:
                    validate_record(row, accepted=True)

                review = {row["id"]: row for row in read_jsonl(queue_path)}
                self.assertEqual(review["canon.bulk.official"]["status"], "accepted")
                self.assertEqual(review["canon.bulk.inference"]["status"], "accepted")
                self.assertEqual(review["canon.bulk.curated"]["status"], "accepted")
                self.assertEqual(review["canon.bulk.duplicate"]["status"], "candidate")
                self.assertEqual(review["canon.bulk.suppressed"]["status"], "suppressed")
                self.assertEqual(review["canon.bulk.conflict"]["status"], "candidate")
                self.assertEqual(review["canon.other.outside"]["status"], "candidate")

    def test_requires_explicit_scope_and_kr_confirmation(self):
        queue = [candidate("canon.bulk.direct")]
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            queue_path = base / "review.jsonl"
            runtime_path = base / "lore.jsonl"
            write_jsonl(queue_path, queue)
            with (patch.object(lore_pipeline, "QUEUE_PATH", queue_path),
                  patch.object(lore_pipeline, "RUNTIME_PATH", runtime_path)):
                with self.assertRaises(SystemExit):
                    lore_cli.approve_all(args(id_prefix=None))
                with self.assertRaises(ValueError):
                    lore_cli.approve_all(args(confirm_kr_release=False))
                self.assertEqual(read_jsonl(queue_path), queue)
                self.assertEqual(read_jsonl(runtime_path), [])

    def test_parser_exposes_approve_all(self):
        parsed = lore_cli.parser().parse_args([
            "approve-all",
            "--source-type", "curated_research",
            "--confirm-kr-release",
            "--dry-run",
        ])
        self.assertIs(parsed.run, lore_cli.approve_all)
        self.assertEqual(parsed.source_type, "curated_research")
        self.assertTrue(parsed.dry_run)


if __name__ == "__main__":
    unittest.main()
