import pytest
from fastapi.testclient import TestClient

from qecho.app import create_app

AUTH = {"Authorization": "Bearer simple-api-admin-token-12345"}
EVENT_AUTH = {"Authorization": "Bearer simple-api-event-token-12345"}


class Model:
    def __init__(self):
        self.prompts = []

    async def generate(self, settings, messages, api_key):
        self.prompts.append(messages[0]["content"])
        return "好呀，听你说。"

    async def close(self):
        pass


class Bridge:
    def __init__(self):
        self.sent = []

    async def login_info(self, base_url, token):
        return "12345678"

    async def friends(self, base_url, token):
        return [{"qq_id": "11111111", "name": "小夏"}, {"qq_id": "22222222", "name": "小林"}]

    async def send_private(self, base_url, token, user_id, text):
        self.sent.append((user_id, text))
        return str(100 + len(self.sent))

    async def close(self):
        pass


@pytest.fixture
def app_client(tmp_path, monkeypatch):
    monkeypatch.setenv("QECHO_ADMIN_TOKEN", "simple-api-admin-token-12345")
    monkeypatch.setenv("QECHO_WEBHOOK_TOKEN", "simple-api-event-token-12345")
    model, bridge = Model(), Bridge()
    with TestClient(create_app(tmp_path, model, bridge)) as client:
        client.patch(
            "/api/settings",
            headers=AUTH,
            json={
                "model_mode": "live",
                "model_name": "local-test",
                "model_base_url": "http://local.invalid/v1",
                "reply_delay_seconds": 0,
            },
        ).raise_for_status()
        yield client, model, bridge


def start(client, qq_ids=None):
    synced = client.post("/api/qq/sync", headers=AUTH).json()
    return client.post(
        "/api/automation/start",
        headers=AUTH,
        json={
            "qq_ids": ["11111111"] if qq_ids is None else qq_ids,
            "prompt": "语气轻松、温柔，简短回复，不要连续追问。",
            "expected_revision": synced["revision"],
        },
    )


def incoming(client, user_id, message_id):
    return client.post(
        "/webhooks/onebot",
        headers=EVENT_AUTH,
        json={
            "self_id": 12345678,
            "post_type": "message",
            "message_type": "private",
            "sub_type": "friend",
            "user_id": user_id,
            "message_id": message_id,
            "message": "今天有点累。",
        },
    )


def test_new_workflow_sync_select_prompt_and_auto_reply(app_client):
    client, model, bridge = app_client
    synced = client.post("/api/qq/sync", headers=AUTH)
    assert synced.status_code == 200
    assert synced.json()["account_id"] == "12345678"
    assert len(synced.json()["friends"]) == 2
    assert synced.json()["selected_ids"] == []
    assert not synced.json()["running"]
    response = start(client)
    assert response.status_code == 200
    assert response.json()["running"] and response.json()["selected_ids"] == ["11111111"]
    assert incoming(client, 22222222, 1).json()["status"] == "ignored"
    assert incoming(client, 11111111, 2).json()["status"] == "accepted"
    assert bridge.sent == [("11111111", "好呀，听你说。")]
    assert "温柔" in model.prompts[-1]
    status = client.get("/api/automation", headers=AUTH).json()
    assert status["recent"][0]["status"] == "sent"


def test_stop_blocks_later_events_and_invalid_selection_stays_stopped(app_client):
    client, _, bridge = app_client
    assert start(client).status_code == 200
    stopped = client.post("/api/automation/stop", headers=AUTH)
    assert stopped.status_code == 200 and not stopped.json()["running"]
    assert incoming(client, 11111111, 3).json()["status"] == "ignored"
    assert not bridge.sent
    assert start(client, ["99999999"]).status_code == 409
    assert not client.get("/api/automation", headers=AUTH).json()["running"]


def test_new_endpoints_are_authenticated_and_setup_reveals_only_event_token(app_client):
    client, _, _ = app_client
    for method, path in [
        ("GET", "/api/setup"),
        ("GET", "/api/automation"),
        ("POST", "/api/qq/sync"),
        ("POST", "/api/automation/stop"),
    ]:
        assert client.request(method, path).status_code == 401
    setup = client.get("/api/setup", headers=AUTH).json()
    assert setup["webhook_token"] == "simple-api-event-token-12345"
    assert "admin" not in str(setup)
    status = client.get("/api/automation", headers=AUTH).text
    assert "simple-api-event-token" not in status


def test_start_requires_nonempty_scope_and_prompt(app_client):
    client, _, _ = app_client
    revision = client.get("/api/automation", headers=AUTH).json()["revision"]
    response = client.post(
        "/api/automation/start",
        headers=AUTH,
        json={
            "qq_ids": [],
            "prompt": "",
            "expected_revision": revision,
        },
    )
    assert response.status_code == 422
