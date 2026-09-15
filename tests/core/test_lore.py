import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch

from hina_bot.core.lore import LoreIndex, LoreValidationError, validate_record
from hina_bot.tooling import lore_pipeline
from hina_bot.tooling.lore_pipeline import _source_rows, _typed_source_rows


def record(identifier="canon.test", lane="canon", **overrides):
    value = {
        "id": identifier, "lane": lane, "summary": "마코토는 만마전 의장이다.",
        "keywords": ["만마전", "의장"], "subjects": ["마코토"],
        "knowledge": "public_knowledge", "confidence": "verified", "status": "accepted",
        "kr_release": "confirmed" if lane == "canon" else "not_applicable",
        "timeline": "한국 서버 최신 검증 시점",
        "source": {"type": "official_game", "title": "스토리", "locator": "1장"},
    }
    if lane == "community_meme":
        value["reaction"] = "짧게 반응하고 본론을 돕는다."
    value.update(overrides)
    return value


class LoreValidationTests(unittest.TestCase):
    def test_runtime_rejects_candidate_and_meme_without_reaction(self):
        with self.assertRaises(LoreValidationError):
            validate_record(record(confidence="candidate"), accepted=True)
        with self.assertRaises(LoreValidationError):
            validate_record(record("meme.test", "community_meme", reaction=""), accepted=True)
        with self.assertRaises(LoreValidationError):
            validate_record(record(kr_release="pending"), accepted=True)

    def test_reference_only_canon_cannot_enter_runtime(self):
        with self.assertRaises(LoreValidationError):
            validate_record(record("canon.anime", fact_type="adaptation"), accepted=True)
        with self.assertRaises(LoreValidationError):
            validate_record(record("canon.fandom", fact_type="fandom"), accepted=True)
        # Existing community meme rows remain valid even though their implicit fact type is fandom.
        validate_record(record("meme.test", "community_meme"), accepted=True)

    def test_load_rejects_duplicate_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "lore.jsonl"
            row = json.dumps(record(), ensure_ascii=False)
            path.write_text(row + "\n" + row + "\n", encoding="utf-8")
            with self.assertRaises(LoreValidationError):
                LoreIndex.load(str(path))

    def test_long_sources_are_split_without_losing_text(self):
        text = "가" * 30_000 + "\n\n" + "나" * 30_000
        rows = _source_rows(text, lane="canon", title="t", url="u",
                            source_type="official", locator="l")
        self.assertGreater(len(rows), 2)
        self.assertEqual("".join(row["text"] for row in rows), text.replace("\n\n", ""))
        self.assertTrue(all(len(row["text"]) <= 24_000 for row in rows))

    def test_curated_fact_labels_are_split_before_extraction(self):
        text = "# header\n\n[FACT_DIRECT]\nid: hina.fact\nstatement: 직접 사실\n\n"
        text += "[INFERENCE]\nid: hina.guess\nstatement: 해석\n\n"
        text += "[UNKNOWN]\nid: hina.unknown\nstatement: 미확인\n"
        rows = _typed_source_rows(
            text, lane="canon", title="curated", url="u", source_type="curated", locator="root")
        self.assertEqual(
            [row["declared_fact_type"] for row in rows],
            ["fact_direct", "inference", "unknown"],
        )
        self.assertIn("hina.fact", rows[0]["locator"])
        self.assertNotIn("# header", rows[0]["text"])


class LoreSearchTests(unittest.TestCase):
    def setUp(self):
        self.index = LoreIndex([
            record(),
            record("meme.head", "community_meme", summary="머리 크기는 커뮤니티 농담이다.",
                   keywords=["머리 크기", "머리 부피"], subjects=["히나"], knowledge="unknown"),
            record("canon.ako", summary="아코는 선도부 행정관이다.", keywords=["행정관", "보좌"],
                   subjects=["아코"]),
        ])

    def test_selects_relevant_canon(self):
        result = self.index.search("마코토는 만마전에서 무슨 일을 해?")
        self.assertEqual(result[0]["reference"], "canon.test")
        self.assertEqual(result[0]["kind"], "world_fact")

    def test_interpretation_and_unknown_are_not_exposed_as_world_facts(self):
        index = LoreIndex([
            record("canon.interpretation", fact_type="inference",
                   summary="히나는 호시노와 자신을 대비했다고 해석할 수 있다.",
                   keywords=["호시노", "대비"], subjects=["호시노"]),
            record("canon.guard", fact_type="unknown", knowledge="unknown",
                   summary="날개가 있는 학생 모두가 날 수 있다고 확정할 근거는 없다.",
                   keywords=["날개", "비행"], subjects=["날개"]),
        ])
        interpretation = index.search("호시노와 대비한 이유")
        self.assertEqual(interpretation[0]["kind"], "interpretation")
        guard = index.search("날개 있으면 다 비행해?")
        self.assertEqual(guard[0]["kind"], "interpretation")
        self.assertEqual(guard[0]["guard"], "do_not_assert_positive_fact")

    def test_reference_only_canon_is_not_retrieved_by_default(self):
        index = LoreIndex([
            record("canon.animation", fact_type="adaptation", summary="애니메이션의 연출이다.",
                   keywords=["애니메이션", "연출"], subjects=["히나"]),
        ])
        self.assertEqual(index.search("히나 애니메이션 연출"), [])
        result = index.search("히나 애니메이션 연출", include_reference_only=True)
        self.assertEqual(result[0]["source_scope"], "adaptation")
        self.assertEqual(result[0]["kind"], "interpretation")

    def test_community_lane_is_labeled_and_can_be_disabled(self):
        result = self.index.search("히나 머리 부피를 구하자")
        self.assertEqual(result[0]["kind"], "optional_reaction")
        self.assertNotRegex(str(result[0]), "공식|커뮤니티|밈|meme")
        self.assertNotIn("optional_reaction", str(self.index.search(
            "히나 머리 부피를 구하자", include_community=False)))

    def test_generic_hina_does_not_retrieve_every_meme(self):
        self.assertEqual(self.index.search("히나야 안녕"), [])

    def test_budget_and_limit_are_enforced(self):
        self.assertEqual(len(self.index.search("마코토 아코", limit=1)), 1)
        self.assertEqual(self.index.search("마코토", chars=1), [])

    def test_packaged_corpus_is_valid_and_searchable(self):
        packaged = LoreIndex.load()
        self.assertTrue(packaged.records)
        self.assertEqual(len({row["id"] for row in packaged.records}), len(packaged.records))
        self.assertTrue(all(row["status"] == "accepted" for row in packaged.records))
        self.assertTrue(any(row["lane"] == "canon" for row in packaged.records))
        result = packaged.search("히나는 게헨나 선도부장이야?")
        self.assertTrue(result)
        self.assertTrue(any(item["kind"] == "world_fact" for item in result))


class LorePipelineTests(unittest.TestCase):
    def test_extract_stays_candidate_and_canon_approval_needs_kr_confirmation(self):
        parsed = {"candidates": [{
            "slug": "hina.test-fact", "summary": "히나는 시험 설정을 알고 있다.",
            "fact_type": "fact_direct", "keywords": ["시험 설정"], "subjects": ["히나"],
            "knowledge": "self", "reaction": "", "evidence": "장면 일부", "uncertainty": "",
            "timeline": "테스트 장면", "kr_release_evidence": "한국 공지",
        }]}
        response = NS(status="completed", output_text=json.dumps(parsed, ensure_ascii=False))
        fake_responses = NS(create=lambda **kwargs: response)
        fake_client = NS(responses=fake_responses)
        raw = [{"source_id": "source-001", "lane": "canon", "title": "테스트",
                "url": "https://example.com", "source_type": "official_game",
                "locator": "1화", "text": "테스트 원문"}]
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            raw_path, queue_path, runtime_path = (base / "raw.jsonl", base / "queue.jsonl",
                                                   base / "runtime.jsonl")
            lore_pipeline.write_jsonl(raw_path, raw)
            with (patch.object(lore_pipeline, "RAW_PATH", raw_path),
                  patch.object(lore_pipeline, "QUEUE_PATH", queue_path),
                  patch.object(lore_pipeline, "RUNTIME_PATH", runtime_path),
                  patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}),
                  patch("openai.OpenAI", return_value=fake_client)):
                lore_pipeline.extract(NS(all=True, source_id=None, model="test-model"))
                candidate = lore_pipeline.read_jsonl(queue_path)[0]
                self.assertEqual(candidate["status"], "candidate")
                self.assertEqual(candidate["fact_type"], "fact_direct")
                self.assertEqual(candidate["kr_release"], "pending")
                with self.assertRaises(SystemExit):
                    lore_pipeline.decide(NS(id=candidate["id"], confidence="verified",
                                            confirm_kr_release=False), "accepted")
                lore_pipeline.decide(NS(id=candidate["id"], confidence="verified",
                                        confirm_kr_release=True), "accepted")
                accepted = lore_pipeline.read_jsonl(runtime_path)[0]
                self.assertEqual(accepted["kr_release"], "confirmed")
                self.assertEqual(accepted["fact_type"], "fact_direct")
                validate_record(accepted, accepted=True)

    def test_declared_fact_type_overrides_model_and_reference_only_is_suppressed(self):
        parsed = {"candidates": [{
            "slug": "hina.anim", "summary": "애니메이션에서 날개 크기가 바뀐다.",
            "fact_type": "fact_direct", "keywords": ["날개"], "subjects": ["히나"],
            "knowledge": "self", "reaction": "", "evidence": "7화", "uncertainty": "",
            "timeline": "The Animation 7화", "kr_release_evidence": "",
        }]}
        response = NS(status="completed", output_text=json.dumps(parsed, ensure_ascii=False))
        fake_client = NS(responses=NS(create=lambda **kwargs: response))
        raw = [{
            "source_id": "source-typed", "lane": "canon", "title": "분류 자료", "url": "",
            "source_type": "curated", "locator": "날개", "text": "typed",
            "declared_fact_type": "adaptation",
        }]
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            raw_path, queue_path, runtime_path = (base / "raw.jsonl", base / "queue.jsonl",
                                                   base / "runtime.jsonl")
            lore_pipeline.write_jsonl(raw_path, raw)
            with (patch.object(lore_pipeline, "RAW_PATH", raw_path),
                  patch.object(lore_pipeline, "QUEUE_PATH", queue_path),
                  patch.object(lore_pipeline, "RUNTIME_PATH", runtime_path),
                  patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}),
                  patch("openai.OpenAI", return_value=fake_client)):
                lore_pipeline.extract(NS(all=True, source_id=None, model="test-model"))
                candidate = lore_pipeline.read_jsonl(queue_path)[0]
                self.assertEqual(candidate["fact_type"], "adaptation")
                self.assertEqual(candidate["status"], "suppressed")
                with self.assertRaises(SystemExit):
                    lore_pipeline.decide(NS(id=candidate["id"], confidence="crosschecked",
                                            confirm_kr_release=True), "accepted")
                self.assertEqual(lore_pipeline.read_jsonl(runtime_path), [])

    def test_edit_candidate_updates_review_fields_before_approval(self):
        candidate = record(
            "canon.hina.test-edit",
            summary="수정 전 요약",
            keywords=["수정 전"],
            subjects=["히나"],
            knowledge="public_knowledge",
            confidence="candidate",
            status="candidate",
            kr_release="pending",
            timeline="수정 전 시점",
            source_id="source-001",
            evidence="근거",
            uncertainty="",
            kr_release_evidence="한국 서버 확인 필요",
        )
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            queue_path, runtime_path = base / "queue.jsonl", base / "runtime.jsonl"
            lore_pipeline.write_jsonl(queue_path, [candidate])
            with (patch.object(lore_pipeline, "QUEUE_PATH", queue_path),
                  patch.object(lore_pipeline, "RUNTIME_PATH", runtime_path)):
                lore_pipeline.edit_candidate(NS(
                    id=candidate["id"],
                    summary="히나는 자신의 수영 실력을 알고 있다.",
                    knowledge="self",
                    timeline="수영복 이벤트 이전부터의 자기 정보",
                    keyword=["수영", "수영 실력"],
                    subject=["히나"],
                ))
                edited = lore_pipeline.read_jsonl(queue_path)[0]
                self.assertEqual(edited["summary"], "히나는 자신의 수영 실력을 알고 있다.")
                self.assertEqual(edited["knowledge"], "self")
                self.assertEqual(edited["keywords"], ["수영", "수영 실력"])
                self.assertEqual(edited["subjects"], ["히나"])
                lore_pipeline.decide(NS(id=candidate["id"], confidence="crosschecked",
                                        confirm_kr_release=True), "accepted")
                accepted = lore_pipeline.read_jsonl(runtime_path)[0]
                self.assertEqual(accepted["summary"], edited["summary"])
                self.assertEqual(accepted["knowledge"], "self")

    def test_edit_can_promote_suppressed_only_by_reclassifying_it(self):
        candidate = record(
            "canon.hina.reclassify",
            fact_type="fandom",
            confidence="candidate",
            status="suppressed",
            kr_release="pending",
            source_id="source-003",
            evidence="근거",
            uncertainty="",
            kr_release_evidence="한국 서버 확인",
        )
        with tempfile.TemporaryDirectory() as directory:
            queue_path = Path(directory) / "queue.jsonl"
            lore_pipeline.write_jsonl(queue_path, [candidate])
            with patch.object(lore_pipeline, "QUEUE_PATH", queue_path):
                lore_pipeline.edit_candidate(NS(
                    id=candidate["id"], summary=None, knowledge=None, timeline=None,
                    fact_type="fact_direct", keyword=None, subject=None,
                ))
                edited = lore_pipeline.read_jsonl(queue_path)[0]
                self.assertEqual(edited["fact_type"], "fact_direct")
                self.assertEqual(edited["status"], "candidate")

    def test_edit_rejects_non_candidate_and_empty_changes(self):
        accepted = record("canon.accepted")
        candidate = record(
            "canon.candidate",
            confidence="candidate",
            status="candidate",
            kr_release="pending",
            source_id="source-002",
            evidence="근거",
            uncertainty="",
            kr_release_evidence="",
        )
        with tempfile.TemporaryDirectory() as directory:
            queue_path = Path(directory) / "queue.jsonl"
            lore_pipeline.write_jsonl(queue_path, [accepted, candidate])
            with patch.object(lore_pipeline, "QUEUE_PATH", queue_path):
                with self.assertRaises(SystemExit):
                    lore_pipeline.edit_candidate(NS(
                        id=accepted["id"], summary="변경", knowledge=None,
                        timeline=None, keyword=None, subject=None,
                    ))
                with self.assertRaises(SystemExit):
                    lore_pipeline.edit_candidate(NS(
                        id=candidate["id"], summary=None, knowledge=None,
                        timeline=None, keyword=None, subject=None,
                    ))


if __name__ == "__main__":
    unittest.main()
