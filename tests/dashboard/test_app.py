from fastapi.testclient import TestClient

from hina_bot.core.store import Store
from hina_bot.dashboard.app import create_app
from hina_bot.dashboard.config import DashboardSettings


def test_dashboard_health_uses_read_only_sources(tmp_path):
    database = tmp_path / "hina.sqlite3"
    Store(str(database)).close()
    usage = tmp_path / "usage.jsonl"
    events = tmp_path / "events.jsonl"
    usage.write_text("", encoding="utf-8")
    events.write_text("", encoding="utf-8")

    app = create_app(
        DashboardSettings(
            database_path=str(database),
            usage_log_path=str(usage),
            event_log_path=str(events),
        )
    )
    response = TestClient(app).get("/healthz")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "telemetry_sources": {
            "usage_files": 1,
            "exchange_files": 0,
            "event_files": 1,
        },
    }
