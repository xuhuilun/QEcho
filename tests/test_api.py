import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient

from qecho.app import create_app

ADMIN = "local-test-admin-token-12345"
WEBHOOK = "local-test-event-token-67890"
AUTH = {"Authorization": f"Bearer {ADMIN}"}


class LocalModel:
    def __init__(self):
        self.calls = []

    async def generate(self, settings, messages, api_key):
        self.calls.append(messages)
        return "好呀，周末一起去看看。"

    async def close(self):
        pass


class LocalBridge:
    def __init__(self):
        self.sent = []

    async def send_private(self, base_url, token, user_id, text):
        self.sent.append((user_id, text))
        return str(9000 + len(self.sent))

    async def friends(self, base_url, token):
        return [{"qq_id": "7654321", "name": "小夏"}]

    async def login_info(self, base_url, token):
        return "12345678"

    async def close(self):
        pass


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("QECHO_ADMIN_TOKEN", ADMIN)
    monkeypatch.setenv("QECHO_WEBHOOK_TOKEN", WEBHOOK)
    model, bridge = LocalModel(), LocalBridge()
    app = create_app(tmp_path, model, bridge)
    with TestClient(app) as client:
        yield client, model, bridge


def setup_contact(client, *, enabled=True):
    response = client.patch(
        "/api/settings",
        headers=AUTH,
        json={
            "enabled": enabled,
            "model_mode": "live",
            "qq_self_id": "12345678",
            "model_base_url": "http://model.invalid/v1",
            "model_name": "test-model",
            "reply_delay_seconds": 0,
        },
    )
    assert response.status_code == 200
    response = client.post(
        "/api/contacts",
        headers=AUTH,
        json={
            "qq_id": "7654321",
            "name": "小夏",
            "enabled": True,
            "persona": "温柔坦诚，聊到喜欢的事情会分享自己的想法。",
            "tone": "简短、自然、不油腻",
            "relationship": "喜欢的人",
        },
    )
    assert response.status_code == 201
    return response.json()


def event(client, message_id=1):
    payload = {
        "self_id": 12345678,
        "post_type": "message",
        "message_type": "private",
        "sub_type": "friend",
        "user_id": 7654321,
        "message_id": message_id,
        "message": [{"type": "text", "data": {"text": "周末去逛书店吗？"}}],
    }
    raw = json.dumps(payload, ensure_ascii=False).encode()
    signature = "sha1=" + hmac.new(WEBHOOK.encode(), raw, hashlib.sha1).hexdigest()
    return client.post(
        "/webhooks/onebot",
        content=raw,
        headers={
            "Content-Type": "application/json",
            "X-Signature": signature,
        },
    )


def test_auth_and_no_secret_disclosure(api):
    client, _, _ = api
    assert client.get("/healthz").json() == {"status": "ok"}
    assert client.get("/api/settings").status_code == 401
    assert (
        client.get("/api/contacts", headers={"Authorization": f"Bearer {WEBHOOK}"}).status_code
        == 401
    )
    result = client.patch(
        "/api/settings",
        headers=AUTH,
        json={
            "model_api_key": "private-model-key",
            "bridge_token": "private-bridge-key",
        },
    )
    assert result.status_code == 200
    assert result.json()["model_api_key_set"] is True
    assert "private-model-key" not in result.text
    assert "private-bridge-key" not in client.get("/api/settings", headers=AUTH).text
    assert result.headers["Cache-Control"] == "no-store"
    result = client.patch("/api/settings", headers=AUTH, json={"clear_model_api_key": True})
    assert result.json()["model_api_key_set"] is False


def test_non_ascii_authentication_returns_401(api):
    client, _, _ = api
    assert (
        client.get("/api/settings", headers=[(b"authorization", b"Bearer \xff")]).status_code == 401
    )
    assert (
        client.post(
            "/webhooks/onebot", content=b"{}", headers=[(b"x-signature", b"sha1=\xff")]
        ).status_code
        == 401
    )


def test_signature_is_over_exact_body_and_separate_from_admin(api):
    client, _, _ = api
    setup_contact(client)
    assert event(client).json()["status"] == "accepted"
    assert event(client).json()["status"] == "duplicate"
    assert client.post("/webhooks/onebot", json={}, headers=AUTH).status_code == 401
    assert (
        client.post(
            "/webhooks/onebot", content=b"{}", headers={"X-Signature": "sha1=wrong"}
        ).status_code
        == 401
    )
    assert (
        client.post(
            "/webhooks/onebot", content=b"broken", headers={"Authorization": f"Bearer {WEBHOOK}"}
        ).status_code
        == 400
    )
    assert (
        client.post(
            "/webhooks/onebot", json=[], headers={"Authorization": f"Bearer {WEBHOOK}"}
        ).status_code
        == 400
    )


def test_real_draft_manual_edit_send_is_idempotent(api):
    client, model, bridge = api
    contact = setup_contact(client)
    assert event(client).status_code == 200
    messages = client.get(f"/api/contacts/{contact['id']}/messages", headers=AUTH).json()
    draft = messages[-1]
    assert draft["status"] == "draft" and not bridge.sent
    assert "温柔坦诚" in model.calls[-1][0]["content"]
    response = client.post(
        f"/api/messages/{draft['id']}/send",
        headers=AUTH,
        json={"content": "好呀，下午两点怎么样？"},
    )
    assert response.json()["status"] == "sent"
    again = client.post(f"/api/messages/{draft['id']}/send", headers=AUTH, json={})
    assert again.json()["status"] == "sent"
    assert bridge.sent == [("7654321", "好呀，下午两点怎么样？")]


def test_simulation_isolated_and_never_sendable(api):
    client, _, bridge = api
    contact = setup_contact(client, enabled=False)
    result = client.post(
        f"/api/contacts/{contact['id']}/simulate",
        headers=AUTH,
        json={"text": "只有演练才知道的消息"},
    )
    assert result.status_code == 200
    assert result.json()["source"] == "simulator"
    send = client.post(f"/api/messages/{result.json()['id']}/send", headers=AUTH, json={})
    assert send.status_code == 409 and not bridge.sent
    preview = client.get(f"/api/contacts/{contact['id']}/prompt", headers=AUTH).json()
    assert "只有演练才知道的消息" not in json.dumps(preview, ensure_ascii=False)


def test_pause_cancels_draft_and_resume_does_not_revive(api):
    client, _, bridge = api
    contact = setup_contact(client)
    event(client)
    records = client.get(f"/api/contacts/{contact['id']}/messages", headers=AUTH).json()
    draft = records[-1]
    client.patch(f"/api/contacts/{contact['id']}", headers=AUTH, json={"paused": True})
    client.patch(f"/api/contacts/{contact['id']}", headers=AUTH, json={"paused": False})
    assert (
        client.post(f"/api/messages/{draft['id']}/send", headers=AUTH, json={}).status_code == 409
    )
    assert not bridge.sent
    regenerated = client.post(f"/api/messages/{draft['id']}/regenerate", headers=AUTH)
    assert regenerated.status_code == 200
    assert regenerated.json()["status"] == "draft"
    assert regenerated.json()["id"] != draft["id"]


def test_contact_validation_and_removal(api):
    client, _, _ = api
    contact = setup_contact(client)
    assert (
        client.post(
            "/api/contacts", headers=AUTH, json={"qq_id": "7654321", "name": "重复"}
        ).status_code
        == 409
    )
    assert (
        client.post(
            "/api/contacts", headers=AUTH, json={"qq_id": "abc", "name": "无效"}
        ).status_code
        == 422
    )
    assert (
        client.patch(
            f"/api/contacts/{contact['id']}", headers=AUTH, json={"qq_id": "9999999"}
        ).status_code
        == 409
    )
    assert client.delete(f"/api/contacts/{contact['id']}", headers=AUTH).status_code == 200
    assert client.get(f"/api/contacts/{contact['id']}/messages", headers=AUTH).status_code == 404


def test_static_shell_and_friend_import_are_available(api):
    client, _, _ = api
    setup_contact(client)
    response = client.get("/")
    assert response.status_code == 200 and "QEcho" in response.text
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
    assert client.get("/api/bridge/friends", headers=AUTH).json() == [
        {"qq_id": "7654321", "name": "小夏"}
    ]


def test_friend_import_refuses_other_account(api):
    client, _, _ = api
    assert client.get("/api/bridge/friends", headers=AUTH).status_code == 409
