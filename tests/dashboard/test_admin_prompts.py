import sqlite3
from types import SimpleNamespace as NS

import pytest
from fastapi.testclient import TestClient

from hina_bot.core.admin_commands import AdminCommand
from hina_bot.core.admin_db import AdminDatabase
from hina_bot.core.instructions import InstructionRegistry
from hina_bot.core.runtime_knowledge import RuntimeKnowledgeRegistry
from hina_bot.core.store import Store
from hina_bot.dashboard.app import create_app
from hina_bot.dashboard.config import DashboardSettings
from hina_bot.discord.admin_commands import execute_admin_command


@pytest.mark.asyncio
async def test_prompt_state_admin_commands_use_runtime_registries():
    database = AdminDatabase(":memory:")
    instructions = InstructionRegistry(database)
    facts = RuntimeKnowledgeRegistry(database, kind="world_fact")
    interpretations = RuntimeKnowledgeRegistry(database, kind="interpretation")
    client = NS(
        llm=NS(
            instructions=instructions,
            runtime_lore=facts,
            story_context=interpretations,
        )
    )

    await execute_admin_command(
        client,
        AdminCommand(
            id=1,
            request_id="a" * 32,
            actor="local-dashboard",
            action="instruction.add",
            target="test.rule",
            payload={"id": "test.rule", "text": "테스트 지침"},
        ),
    )
    assert instructions.get("test.rule")["text"] == "테스트 지침"

    await execute_admin_command(
        client,
        AdminCommand(
            id=2,
            request_id="b" * 32,
            actor="local-dashboard",
            action="knowledge.add",
            target="world_fact:test.fact",
            payload={
                "id": "test.fact",
                "kind": "world_fact",
                "content": "테스트 사실",
                "keywords": "테스트, 사실",
                "subjects": "히나",
                "awareness": "public_knowledge",
                "timeline": "현재",
            },
        ),
    )
    item = facts.get("test.fact")
    assert item["content"] == "테스트 사실"
    assert item["keywords"] == ["테스트", "사실"]

    await execute_admin_command(
        client,
        AdminCommand(
            id=3,
            request_id="c" * 32,
            actor="local-dashboard",
            action="instruction.state",
            target="test.rule",
            payload={"id": "test.rule", "enabled": False},
        ),
    )
    assert instructions.get("test.rule")["enabled"] is False
    database.close()


def _seed_dashboard_database(path):
    Store(str(path)).close()
    database = AdminDatabase(str(path))
    InstructionRegistry(database).add("existing.rule", "기존 지침")
    RuntimeKnowledgeRegistry(database, kind="world_fact").add(
        "existing.fact",
        "기존 사실",
        "기존, 사실",
        "히나",
        "public_knowledge",
        "현재",
    )
    database.close()


def test_prompt_state_page_reads_existing_rows(tmp_path):
    database = tmp_path / "hina.sqlite3"
    _seed_dashboard_database(database)
    app = create_app(
        DashboardSettings(
            database_path=str(database),
            usage_log_path=str(tmp_path / "usage.jsonl"),
            event_log_path=str(tmp_path / "events.jsonl"),
        )
    )

    with TestClient(app) as client:
        response = client.get("/admin/prompts")

    assert response.status_code == 200
    assert "existing.rule" in response.text
    assert "existing.fact" in response.text
    assert "기존 지침" in response.text
    assert "기존 사실" in response.text
    assert 'class="prompt-form-grid prompt-instruction-grid"' in response.text
    assert 'class="prompt-field prompt-field-body"' in response.text
    assert 'class="prompt-actions"' in response.text
    assert 'class="scope-target-actions"' not in response.text
    assert 'data-confirm-remove="existing.rule"' in response.text
    assert 'name="confirm"' not in response.text
    assert "prompt-state.js" in response.text


def test_prompt_state_post_only_enqueues_and_remove_requires_confirmation(tmp_path):
    database = tmp_path / "hina.sqlite3"
    _seed_dashboard_database(database)
    app = create_app(
        DashboardSettings(
            database_path=str(database),
            usage_log_path=str(tmp_path / "usage.jsonl"),
            event_log_path=str(tmp_path / "events.jsonl"),
            write_enabled=True,
        )
    )
    csrf = app.state.admin_writer.csrf_token

    with TestClient(app) as client:
        rejected = client.post(
            "/admin/prompts/instruction",
            data={
                "_csrf": csrf,
                "_request_id": "d" * 32,
                "action": "remove",
                "id": "existing.rule",
            },
            follow_redirects=False,
        )
        assert rejected.status_code == 422

        queued = client.post(
            "/admin/prompts/instruction",
            data={
                "_csrf": csrf,
                "_request_id": "e" * 32,
                "action": "edit",
                "id": "existing.rule",
                "text": "변경 지침",
            },
            follow_redirects=False,
        )
        assert queued.status_code == 303

    db = sqlite3.connect(database)
    assert db.execute(
        "SELECT text FROM instructions WHERE id='existing.rule'"
    ).fetchone()[0] == "기존 지침"
    row = db.execute(
        "SELECT action,target,status FROM admin_commands ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert row == ("instruction.edit", "existing.rule", "pending")
    db.close()



def test_prompt_state_command_status_endpoint(tmp_path):
    database = tmp_path / "hina.sqlite3"
    _seed_dashboard_database(database)
    app = create_app(
        DashboardSettings(
            database_path=str(database),
            usage_log_path=str(tmp_path / "usage.jsonl"),
            event_log_path=str(tmp_path / "events.jsonl"),
            write_enabled=True,
        )
    )
    csrf = app.state.admin_writer.csrf_token

    with TestClient(app) as client:
        queued = client.post(
            "/admin/prompts/instruction",
            data={
                "_csrf": csrf,
                "_request_id": "f" * 32,
                "action": "edit",
                "id": "existing.rule",
                "text": "변경 지침",
            },
            follow_redirects=False,
        )
        assert queued.status_code == 303

        db = sqlite3.connect(database)
        command_id = db.execute(
            "SELECT id FROM admin_commands ORDER BY id DESC LIMIT 1"
        ).fetchone()[0]
        db.close()

        status = client.get(
            f"/admin/commands/{command_id}",
            headers={"accept": "application/json"},
        )
        script = client.get("/static/prompt-state.js")

    assert status.status_code == 200
    assert status.json()["status"] == "pending"
    assert status.json()["action"] == "instruction.edit"
    assert "payload_json" not in status.json()
    assert script.status_code == 200
    assert "window.confirm" in script.text
    assert "/admin/commands/" in script.text
