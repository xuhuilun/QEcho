"""File configuration is authoritative across startup, API and incoming events."""

import asyncio
import json
import threading
import time

import pytest
from fastapi.testclient import TestClient

from qecho.app import create_app
from qecho.config import load_config

ADMIN = "file-admin-token-123456789"
EVENT = "file-events-token-12345678"
AUTH = {"Authorization": f"Bearer {ADMIN}"}


class Model:
    def __init__(self):
        self.calls = []

    async def generate(self, settings, messages, api_key):
        self.calls.append((messages, api_key))
        return "明天见。"


class Bridge:
    def __init__(self, wait=False):
        self.sent = []
        self.wait = wait
        self.entered = threading.Event()
        self.cancelled = threading.Event()

    async def login_info(self, base_url, token):
        assert base_url == "http://bridge.invalid:3000" and token == "file-bridge-key"
        if self.wait:
            self.entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                self.cancelled.set()
        return "12345678"

    async def friends(self, base_url, token):
        return [{"qq_id": "7654321", "name": "测试好友"}]

    async def send_private(self, base_url, token, user_id, text):
        self.sent.append((user_id, text))
        return str(9000 + len(self.sent))


def config(tmp_path, auto_start=True):
    path = tmp_path / "qecho.toml"
    path.write_text(
        f'''[server]
admin_token = "{ADMIN}"
data_dir = "state"
[model]
base_url = "http://model.invalid/v1"
name = "test-model"
api_key = "file-model-key"
[qq]
api_url = "http://bridge.invalid:3000"
api_token = "file-bridge-key"
event_token = "{EVENT}"
[agent]
friends = ["7654321"]
prompt = "从文件读取的语气和聊天规则"
auto_start = {str(auto_start).lower()}
reply_delay_seconds = 0
''',
        encoding="utf-8",
    )
    return load_config(path)


def status_until(client, state):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        result = client.get("/api/automation", headers=AUTH).json()
        if result["startup_state"] == state:
            return result
        time.sleep(0.01)
    pytest.fail(f"startup did not reach {state}")


def event(client, user_id, message_id):
    return client.post(
        "/webhooks/onebot",
        content=json.dumps(
            {
                "self_id": 12345678,
                "post_type": "message",
                "message_type": "private",
                "sub_type": "friend",
                "user_id": user_id,
                "message_id": message_id,
                "message": [{"type": "text", "data": {"text": "明天见吗？"}}],
            }
        ),
        headers={"Authorization": f"Bearer {EVENT}"},
    )


def test_complete_file_start_needs_no_web_configuration(tmp_path, monkeypatch):
    monkeypatch.setenv("QECHO_ADMIN_TOKEN", "environment-token-ignored")
    monkeypatch.setenv("QECHO_WEBHOOK_TOKEN", "environment-events-ignored")
    monkeypatch.setenv("QECHO_DATA_DIR", str(tmp_path / "wrong-state"))
    cfg = config(tmp_path)
    model, bridge = Model(), Bridge()
    with TestClient(create_app(model_client=model, bridge_client=bridge, file_config=cfg)) as api:
        state = status_until(api, "running")
        assert state["running"] and state["account_id"] == "12345678"
        assert state["configured_ids"] == state["selected_ids"] == ["7654321"]
        assert state["config_source"] == "file" and state["config_file"] == "qecho.toml"
        assert state["prompt"] == cfg.agent.prompt
        assert "file-model-key" not in api.get("/api/settings", headers=AUTH).text
        assert api.get("/api/setup", headers=AUTH).json()["webhook_token"] == EVENT
        assert (
            api.get(
                "/api/automation", headers={"Authorization": "Bearer environment-token-ignored"}
            ).status_code
            == 401
        )
        assert event(api, 7654321, 1).json()["status"] == "accepted"
        assert event(api, 8765432, 2).json()["status"] == "ignored"
        assert bridge.sent == [("7654321", "明天见。")]
        assert cfg.agent.prompt in model.calls[0][0][0]["content"]
        assert model.calls[0][1] == "file-model-key"
        stopped = api.post("/api/automation/stop", headers=AUTH).json()
        assert stopped["startup_state"] == "stopped" and not stopped["running"]
        assert event(api, 7654321, 3).json()["status"] == "ignored"
        assert len(bridge.sent) == 1
    assert (tmp_path / "state/qecho.sqlite3").exists()
    assert not (tmp_path / "wrong-state").exists()


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("PATCH", "/api/settings", {"enabled": True}),
        ("POST", "/api/contacts", {"qq_id": "8765432", "name": "其他好友"}),
        ("PATCH", "/api/contacts/missing", {"enabled": True}),
        ("DELETE", "/api/contacts/missing", None),
        ("DELETE", "/api/contacts/missing/messages", None),
        ("POST", "/api/contacts/missing/simulate", {"text": "test"}),
        ("POST", "/api/messages/missing/send", {}),
        ("POST", "/api/messages/missing/cancel", {}),
        ("POST", "/api/messages/missing/regenerate", {}),
        (
            "POST",
            "/api/automation/start",
            {"qq_ids": ["8765432"], "prompt": "绕过文件", "expected_revision": 1},
        ),
    ],
)
def test_file_mode_denies_web_writes(tmp_path, method, path, body):
    cfg = config(tmp_path, auto_start=False)
    with TestClient(create_app(bridge_client=Bridge(), file_config=cfg)) as api:
        assert api.request(method, path, json=body).status_code == 401
        result = api.request(method, path, json=body, headers=AUTH)
        assert result.status_code == 409 and "配置文件" in result.json()["detail"]
        assert not api.get("/api/automation", headers=AUTH).json()["running"]
        assert api.post("/api/qq/sync", headers=AUTH).status_code == 200


def test_file_stop_does_not_wait_for_login_and_never_rearms(tmp_path):
    cfg = config(tmp_path)
    bridge = Bridge(wait=True)
    with TestClient(create_app(bridge_client=bridge, file_config=cfg)) as api:
        assert bridge.entered.wait(timeout=2)
        assert api.get("/healthz").json() == {"status": "ok"}
        started = time.monotonic()
        response = api.post("/api/automation/stop", headers=AUTH)
        assert time.monotonic() - started < 1
        assert response.json()["startup_state"] == "stopped"
        assert bridge.cancelled.wait(timeout=2)
        bridge.wait = False
        assert api.post("/api/qq/sync", headers=AUTH).status_code == 200
        result = api.get("/api/automation", headers=AUTH).json()
        assert not result["running"] and result["startup_state"] == "stopped"
        assert result["configured_ids"] == ["7654321"] and not bridge.sent


def test_blank_file_tokens_ignore_environment_and_generate_files(tmp_path, monkeypatch):
    monkeypatch.setenv("QECHO_ADMIN_TOKEN", ADMIN)
    monkeypatch.setenv("QECHO_WEBHOOK_TOKEN", EVENT)
    path = tmp_path / "qecho.toml"
    path.write_text("[agent]\nauto_start = false\n", encoding="utf-8")
    cfg = load_config(path)
    with TestClient(create_app(file_config=cfg)) as api:
        assert api.get("/api/automation", headers=AUTH).status_code == 401
        generated = (cfg.data_directory / "admin-token").read_text()
        auth = {"Authorization": f"Bearer {generated}"}
        assert api.get("/api/automation", headers=auth).json()["startup_state"] == "disabled"
        assert api.get("/api/setup", headers=auth).json()["webhook_token"] != EVENT
