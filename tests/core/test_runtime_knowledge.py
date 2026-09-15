import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

from hina_bot.core.admin_db import AdminDatabase
from hina_bot.core.knowledge_ingest import KnowledgeIngestor
from hina_bot.core.runtime_knowledge import RuntimeKnowledgeRegistry
from hina_bot.discord.knowledge_commands import KnowledgeCommands


class RuntimeKnowledgeRegistryTests(unittest.TestCase):
    def test_fact_crud_and_search(self):
        with tempfile.TemporaryDirectory() as directory:
            database = AdminDatabase(str(Path(directory) / "hina.sqlite3"))
            registry = RuntimeKnowledgeRegistry(database, kind="world_fact")
            registry.add(
                "hina.quote", "히나는 사건 뒤 선생과 대화했다.",
                "에덴조약,선생,대화", "히나,선생", "self", "에덴조약 이후")

            item = registry.search("에덴조약 뒤 선생과 무슨 대화를 했어?")[0]
            self.assertEqual(item["kind"], "world_fact")
            self.assertEqual(item["awareness"], "self")
            self.assertTrue(item["reference"].startswith("runtime_lore."))

            registry.edit("hina.quote", content="히나는 회복한 선생과 대화했다.")
            self.assertIn("회복한", registry.get("hina.quote")["content"])
            registry.set_enabled("hina.quote", False)
            self.assertEqual(registry.search("에덴조약 선생"), [])
            registry.remove("hina.quote")
            self.assertEqual(registry.list(), [])
            database.close()

    def test_context_is_marked_as_non_fact_interpretation(self):
        database = AdminDatabase(":memory:")
        registry = RuntimeKnowledgeRegistry(database, kind="interpretation")
        registry.add(
            "hina.hoshino", "히나가 호시노를 비교 대상으로 든 이유에 대한 해석이다.",
            "호시노처럼,호시노,비교", "히나,호시노", "inference", "에덴조약 이후")
        item = registry.search("호시노처럼 될 수 없다는 말이 무슨 뜻이야?")[0]
        self.assertEqual(item["kind"], "interpretation")
        self.assertEqual(item["certainty"], "plausible_interpretation_not_established_fact")
        self.assertTrue(item["reference"].startswith("runtime_context."))
        database.close()

    def test_validation(self):
        registry = RuntimeKnowledgeRegistry(None, kind="world_fact")
        with self.assertRaises(ValueError):
            registry.add("x", "내용", "키워드", "대상", "self")
        database = AdminDatabase(":memory:")
        registry = RuntimeKnowledgeRegistry(database, kind="world_fact")
        with self.assertRaises(ValueError):
            registry.add("valid.id", "내용", "", "히나", "self")
        with self.assertRaises(ValueError):
            registry.add("valid.id", "내용", "키워드", "히나", "bad-awareness")
        database.close()

    def test_legacy_import_keeps_unknown_created_at_null(self):
        database = AdminDatabase(":memory:")
        registry = RuntimeKnowledgeRegistry(database, kind="world_fact")
        registry.import_row({
            "id": "legacy.fact",
            "content": "예전 지식",
            "keywords": ["예전"],
            "subjects": ["히나"],
            "awareness": "unknown",
            "timeline": "시점 미지정",
            "enabled": True,
        })
        row = registry.get("legacy.fact")
        self.assertIsNone(row["created_at"])
        self.assertIsNone(row["updated_at"])
        database.close()


class KnowledgeIngestorTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def fake_llm(database, facts, contexts, payload, records=None):
        request = AsyncMock(return_value=NS(
            status="completed", output_text=json.dumps(payload, ensure_ascii=False)))
        return NS(
            admin_db=database,
            runtime_lore=facts,
            story_context=contexts,
            lore=NS(records=records or []),
            usage=NS(request=request),
            client=object(),
            settings=NS(model="test-model"),
        )

    async def test_ingest_splits_fact_interpretation_and_hold(self):
        database = AdminDatabase(":memory:")
        facts = RuntimeKnowledgeRegistry(database, kind="world_fact")
        contexts = RuntimeKnowledgeRegistry(database, kind="interpretation")
        payload = {
            "items": [
                {
                    "id": "eden.hina.hoshino-quote",
                    "kind": "world_fact",
                    "content": "히나는 에덴조약 사태 이후 자신이 호시노처럼 될 수 없다고 말했다.",
                    "keywords": ["호시노처럼", "에덴조약"],
                    "subjects": ["히나", "호시노"],
                    "awareness": "self",
                    "timeline": "에덴조약 사태 이후",
                    "action": "add",
                    "target_id": "",
                    "supersedes": [],
                    "reason": "입력에 명시된 대사 사실",
                },
                {
                    "id": "hina.hoshino-comparison",
                    "kind": "interpretation",
                    "content": "히나는 상실 뒤에도 후배들을 이끄는 호시노를 자신과 대비했을 수 있다.",
                    "keywords": ["호시노", "비교", "유메"],
                    "subjects": ["히나", "호시노", "유메"],
                    "awareness": "inference",
                    "timeline": "에덴조약 사태 이후",
                    "action": "add",
                    "target_id": "",
                    "supersedes": [],
                    "reason": "입력 자체가 추측으로 제시한 동기 해석",
                },
                {
                    "id": "hina.unknown-detail",
                    "kind": "world_fact",
                    "content": "서로 모순되는 세부 정보다.",
                    "keywords": ["세부 정보"],
                    "subjects": ["히나"],
                    "awareness": "unknown",
                    "timeline": "시점 미지정",
                    "action": "hold",
                    "target_id": "",
                    "supersedes": [],
                    "reason": "입력 내부에서 충돌함",
                },
            ]
        }
        result = await KnowledgeIngestor(
            self.fake_llm(database, facts, contexts, payload)).ingest(
                "에덴조약과 호시노에 대한 조사 메모")

        self.assertEqual(len(result["added"]), 2)
        self.assertEqual(len(result["updated"]), 0)
        self.assertEqual(len(result["held"]), 1)
        self.assertEqual(len(facts.list()), 1)
        self.assertEqual(len(contexts.list()), 1)
        self.assertEqual(contexts.list()[0]["awareness"], "inference")
        database.close()

    async def test_ingest_skips_near_duplicate(self):
        database = AdminDatabase(":memory:")
        facts = RuntimeKnowledgeRegistry(database, kind="world_fact")
        contexts = RuntimeKnowledgeRegistry(database, kind="interpretation")
        facts.add(
            "existing.quote", "히나는 에덴조약 이후 선생과 대화하며 호시노처럼 될 수 없다고 말했다.",
            "호시노처럼,에덴조약", "히나,호시노,선생", "self", "에덴조약 이후")
        payload = {"items": [{
            "id": "new.quote",
            "kind": "world_fact",
            "content": "히나는 에덴조약 이후 선생과 대화하며 호시노처럼 될 수 없다고 말했다.",
            "keywords": ["호시노처럼", "에덴조약"],
            "subjects": ["히나", "호시노", "선생"],
            "awareness": "self",
            "timeline": "에덴조약 이후",
            "action": "add",
            "target_id": "",
            "supersedes": [],
            "reason": "명시된 대사",
        }]}
        result = await KnowledgeIngestor(
            self.fake_llm(database, facts, contexts, payload)).ingest("에덴조약 호시노처럼 대사 정리")
        self.assertEqual(result["added"], [])
        self.assertEqual(result["skipped"][0]["existing_id"], "existing.quote")
        self.assertEqual(len(facts.list()), 1)
        database.close()

    async def test_ingest_reconciles_correction_and_supersedes_old_entries(self):
        database = AdminDatabase(":memory:")
        facts = RuntimeKnowledgeRegistry(database, kind="world_fact")
        contexts = RuntimeKnowledgeRegistry(database, kind="interpretation")
        facts.add(
            "ingame_no_exact_model_name",
            "히나가 쓰는 총의 정확한 총기 모델명은 인게임에서 나온 적이 없다.",
            "총기 모델명,MG42,인게임", "히나,기관총", "public_knowledge", "상시")
        contexts.add(
            "hina_mg42_guess",
            "히나가 사용하는 총은 MG42로 추측된다.",
            "MG42,기관총,총", "히나,기관총", "inference", "상시")
        contexts.add(
            "appearance_and_usage_basis",
            "외형과 사용법을 보면 MG42로 추측할 수 있다.",
            "외형,사용법,MG42", "히나,기관총", "inference", "상시")

        payload = {"items": [
            {
                "id": "hina.weapon-name",
                "kind": "world_fact",
                "content": "히나가 사용하는 기관총의 게임 내 이름은 '종막의 디스트로이어'다.",
                "keywords": ["종막의 디스트로이어", "기관총", "무기 이름"],
                "subjects": ["히나", "기관총"],
                "awareness": "self",
                "timeline": "상시",
                "action": "add",
                "target_id": "",
                "supersedes": [],
                "reason": "기존 항목에 없던 게임 내 무기명 사실",
            },
            {
                "id": "real-gun-model-not-explicit",
                "kind": "world_fact",
                "content": "히나의 기관총이 현실의 어떤 총기 모델에 대응되는지는 게임에서 명시되지 않았다.",
                "keywords": ["현실 총기", "MG42", "모델", "명시"],
                "subjects": ["히나", "기관총"],
                "awareness": "public_knowledge",
                "timeline": "상시",
                "action": "update",
                "target_id": "ingame_no_exact_model_name",
                "supersedes": [],
                "reason": "기존 문구의 '총기 모델명'을 현실 대응 모델 미명시로 정확히 정정",
            },
            {
                "id": "hina_mg42_guess",
                "kind": "interpretation",
                "content": "히나의 기관총은 외형과 운용 방식 때문에 현실의 MG42를 모티브로 한 것으로 추측된다.",
                "keywords": ["MG42", "모티브", "외형", "운용 방식"],
                "subjects": ["히나", "기관총", "MG42"],
                "awareness": "inference",
                "timeline": "상시",
                "action": "update",
                "target_id": "hina_mg42_guess",
                "supersedes": ["appearance_and_usage_basis"],
                "reason": "기존 두 해석을 하나의 더 정확한 해석으로 통합",
            },
        ]}
        llm = self.fake_llm(database, facts, contexts, payload)
        result = await KnowledgeIngestor(llm).ingest(
            "히나가 사용하는 기관총의 이름은 게임 기준으로 종막의 디스트로이어이며, "
            "현실의 MG42가 모티브로 추측된다. 현실 대응 총기 모델은 명시되지 않았다.")

        self.assertEqual(len(result["added"]), 1)
        self.assertEqual(len(result["updated"]), 2)
        self.assertEqual(result["removed"], [{
            "id": "appearance_and_usage_basis", "superseded_by": "hina_mg42_guess"}])
        self.assertIn("현실의 어떤 총기", facts.get("ingame_no_exact_model_name")["content"])
        self.assertIn("종막의 디스트로이어", facts.get("hina.weapon-name")["content"])
        self.assertIn("외형과 운용 방식", contexts.get("hina_mg42_guess")["content"])
        with self.assertRaises(ValueError):
            contexts.get("appearance_and_usage_basis")

        request_input = json.loads(llm.usage.request.await_args.kwargs["input"])
        candidate_ids = {row["id"] for row in request_input["existing_dynamic"]}
        self.assertEqual(candidate_ids, {
            "ingame_no_exact_model_name", "hina_mg42_guess", "appearance_and_usage_basis"})
        database.close()


class KnowledgeCommandTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.database = AdminDatabase(":memory:")
        facts = RuntimeKnowledgeRegistry(self.database, kind="world_fact")
        contexts = RuntimeKnowledgeRegistry(self.database, kind="interpretation")
        self.client = NS(
            emoji_admin_ids={100, 101},
            llm=NS(runtime_lore=facts, story_context=contexts),
        )

    def tearDown(self):
        self.database.close()

    async def test_commands_use_bot_admin_allowlist(self):
        group = KnowledgeCommands(self.client)
        denied = NS(user=NS(id=200), response=NS(send_message=AsyncMock()))
        self.assertFalse(await group.interaction_check(denied))
        allowed = NS(user=NS(id=101), response=NS(send_message=AsyncMock()))
        self.assertTrue(await group.interaction_check(allowed))
        self.assertTrue(group.allowed_contexts.guild)
        self.assertTrue(group.allowed_contexts.dm_channel)
        self.assertTrue(group.allowed_installs.guild)

    def test_command_set(self):
        expected = {"ingest", "list", "show", "enable", "disable", "remove"}
        self.assertEqual({command.name for command in KnowledgeCommands(self.client).commands}, expected)


if __name__ == "__main__":
    unittest.main()
