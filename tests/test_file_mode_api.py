"""File configuration is authoritative across startup, API and incoming events."""

import asyncio
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from qecho.app import create_app
from qecho.config import load_config

ADMIN = "file-admin-token-123456789"
EVENT = "file-events-token-12345678"
AUTH = {"Authorization": f"Bearer {ADMIN}"}
PROMPT_API = "/api/automation/prompts"


class Model:
    def __init__(self):
        self.calls = []

    async def generate(self, settings, messages, api_key):
        self.calls.append((messages, api_key))
        return "明天见。"


class Bridge:
    def __init__(self, wait=False, *, friend_ids=("7654321",)):
        self.sent = []
        self.wait = wait
        self.friend_ids = friend_ids
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
        return [{"qq_id": qq_id, "name": "测试好友"} for qq_id in self.friend_ids]

    async def send_private(self, base_url, token, user_id, text):
        self.sent.append((user_id, text))
        return str(9000 + len(self.sent))


def config(tmp_path, auto_start=True, *, default_ids=("7654321",), prompt_sections=""):
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
friends = {json.dumps(default_ids)}
prompt = "从文件读取的语气和聊天规则"
auto_start = {str(auto_start).lower()}
reply_delay_seconds = 0
{prompt_sections}
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


def current_prompts(client):
    response = client.get(PROMPT_API, headers=AUTH)
    assert response.status_code == 200, response.text
    return response.json()


def prompt_edit(revision, scope, prompt, key=""):
    return {"scope": scope, "key": key, "prompt": prompt, "expected_revision": revision}


def save_prompt(client, revision, scope, prompt, key=""):
    response = client.patch(
        PROMPT_API, headers=AUTH, json=prompt_edit(revision, scope, prompt, key)
    )
    assert response.status_code == 200, response.text
    saved = response.json()
    assert saved["revision"] > revision
    return saved


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


PROMPT_GROUPS = """
[[agent.prompt_groups]]
name = "亲近好友"
friends = ["7654322", "7654323"]
prompt = "这一组使用轻松语气。"

[[agent.prompt_groups]]
name = "同学"
friends = ["7654324"]
prompt = "这一组用简洁语气。"

[agent.friend_prompts]
"7654322" = "只有这位好友用昵称小林。"
"""


def test_live_prompt_edits_apply_to_next_messages_and_clear_restores_inheritance(tmp_path):
    cfg = config(tmp_path, prompt_sections=PROMPT_GROUPS)
    model = Model()
    bridge = Bridge(friend_ids=("7654321", "7654322", "7654323", "7654324"))
    with TestClient(create_app(model_client=model, bridge_client=bridge, file_config=cfg)) as api:
        status_until(api, "running")
        original = current_prompts(api)

        saved = save_prompt(api, original["revision"], "global", "使用实时保存的全局规则。")
        assert event(api, 7654321, 1).json()["status"] == "accepted"
        system = model.calls[-1][0][0]["content"]
        assert saved["global_prompt"] in system and cfg.agent.prompt not in system

        saved = save_prompt(api, saved["revision"], "group", "实时修改的组规则。", "亲近好友")
        assert event(api, 7654323, 2).json()["status"] == "accepted"
        system = model.calls[-1][0][0]["content"]
        assert saved["groups"]["亲近好友"] in system
        assert original["groups"]["亲近好友"] not in system

        saved = save_prompt(api, saved["revision"], "friend", "实时修改的个人称呼。", "7654322")
        assert event(api, 7654322, 3).json()["status"] == "accepted"
        system = model.calls[-1][0][0]["content"]
        assert system.index(saved["global_prompt"]) < system.index(saved["groups"]["亲近好友"])
        assert system.index(saved["groups"]["亲近好友"]) < system.index(
            saved["friend_prompts"]["7654322"]
        )
        assert original["friend_prompts"]["7654322"] not in system

        assert event(api, 7654324, 4).json()["status"] == "accepted"
        system = model.calls[-1][0][0]["content"]
        assert original["groups"]["同学"] in system
        assert saved["groups"]["亲近好友"] not in system
        assert saved["friend_prompts"]["7654322"] not in system

        cleared = save_prompt(api, saved["revision"], "friend", "", "7654322")
        assert "7654322" not in cleared["friend_prompts"]
        assert event(api, 7654322, 5).json()["status"] == "accepted"
        system = model.calls[-1][0][0]["content"]
        assert cleared["groups"]["亲近好友"] in system
        assert saved["friend_prompts"]["7654322"] not in system
        assert original["friend_prompts"]["7654322"] not in system
        assert api.get("/api/automation", headers=AUTH).json()["running"]
        assert [qq_id for qq_id, _ in bridge.sent] == [
            "7654321",
            "7654323",
            "7654322",
            "7654324",
            "7654322",
        ]


def test_edit_invalidates_generation_already_in_progress(tmp_path):
    cfg = config(tmp_path)
    model, bridge = Model(), Bridge()
    entered, release = threading.Event(), threading.Event()
    generate = model.generate

    async def gated_generate(*args):
        reply = await generate(*args)
        if len(model.calls) == 1:
            entered.set()
            assert await asyncio.to_thread(release.wait, 8)
        return reply

    model.generate = gated_generate
    with TestClient(create_app(model_client=model, bridge_client=bridge, file_config=cfg)) as api:
        status_until(api, "running")
        with ThreadPoolExecutor(max_workers=1) as requests:
            processing = requests.submit(event, api, 7654321, 1)
            try:
                assert entered.wait(timeout=4)
                assert cfg.agent.prompt in model.calls[0][0][0]["content"]
                original = current_prompts(api)
                saved = save_prompt(api, original["revision"], "global", "生成中保存的新规则。")
            finally:
                release.set()
            assert processing.result(timeout=5).json()["status"] == "accepted"
        assert bridge.sent == []
        assert event(api, 7654321, 2).json()["status"] == "accepted"
        assert saved["global_prompt"] in model.calls[-1][0][0]["content"]
        assert bridge.sent == [("7654321", "明天见。")]


@pytest.mark.parametrize("stage", ["waiting", "starting"])
def test_prompt_saved_while_startup_waits_is_used_by_first_reply(tmp_path, stage):
    cfg = config(tmp_path)
    model, bridge = Model(), Bridge()
    app = create_app(model_client=model, bridge_client=bridge, file_config=cfg)
    entered, release = threading.Event(), threading.Event()
    method = "login_info" if stage == "waiting" else "friends"
    original_call = getattr(bridge, method)

    async def gated_call(*args):
        if app.state.startup.state == stage and not release.is_set():
            entered.set()
            assert await asyncio.to_thread(release.wait, 8)
        return await original_call(*args)

    setattr(bridge, method, gated_call)
    with TestClient(app) as api:
        try:
            assert entered.wait(timeout=4)
            original = current_prompts(api)
            saved = save_prompt(api, original["revision"], "global", "启动等待期间保存的规则。")
        finally:
            release.set()
        status_until(api, "running")
        assert event(api, 7654321, 1).json()["status"] == "accepted"
        system = model.calls[-1][0][0]["content"]
        assert saved["global_prompt"] in system and cfg.agent.prompt not in system


def test_prompt_edits_survive_reopen_until_toml_prompt_definitions_change(tmp_path):
    cfg = config(tmp_path, auto_start=False, prompt_sections=PROMPT_GROUPS)
    original_file = cfg.source_path.read_bytes()
    with TestClient(create_app(file_config=cfg)) as api:
        original = current_prompts(api)
        saved = save_prompt(api, original["revision"], "global", "保留网页全局规则。")
        saved = save_prompt(api, saved["revision"], "group", "保留网页分组规则。", "亲近好友")
        saved = save_prompt(api, saved["revision"], "friend", "保留网页个人规则。", "7654322")
        assert cfg.source_path.read_bytes() == original_file

    def reopen_prompts():
        with TestClient(create_app(file_config=load_config(cfg.source_path))) as api:
            return current_prompts(api)

    prompt_fields = ("global_prompt", "groups", "friend_prompts")
    reopened = reopen_prompts()
    assert all(reopened[key] == saved[key] for key in prompt_fields)

    non_prompt_edit = original_file.replace(b'name = "test-model"', b'name = "renamed-model"')
    cfg.source_path.write_bytes(non_prompt_edit)
    reopened = reopen_prompts()
    assert all(reopened[key] == saved[key] for key in prompt_fields)

    new_default = "以新的 TOML 全局规则为准。"
    cfg.source_path.write_bytes(
        non_prompt_edit.replace(cfg.agent.prompt.encode(), new_default.encode())
    )
    reset = reopen_prompts()
    assert reset["global_prompt"] == new_default
    assert reset["groups"] == original["groups"]
    assert reset["friend_prompts"] == original["friend_prompts"]


def test_prompt_editor_requires_admin_and_cannot_expand_friend_scope(tmp_path):
    cfg = config(tmp_path, prompt_sections=PROMPT_GROUPS)
    model = Model()
    bridge = Bridge(friend_ids=("7654321", "7654322", "7654323", "7654324", "7654325"))
    with TestClient(create_app(model_client=model, bridge_client=bridge, file_config=cfg)) as api:
        status_until(api, "running")
        original = current_prompts(api)
        body = prompt_edit(original["revision"], "global", "保存后的规则。")
        for headers in ({}, {"Authorization": f"Bearer {EVENT}"}):
            assert api.get(PROMPT_API, headers=headers).status_code == 401
            assert api.patch(PROMPT_API, headers=headers, json=body).status_code == 401

        saved = save_prompt(api, original["revision"], "global", "保存后的规则。")
        assert api.patch(PROMPT_API, headers=AUTH, json=body).status_code == 409
        saved = save_prompt(api, saved["revision"], "friend", "给名单内好友新增规则。", "7654321")
        assert saved["friend_prompts"]["7654321"] == "给名单内好友新增规则。"
        for scope, key in (("group", "不存在的组"), ("friend", "7654325")):
            attempt = prompt_edit(saved["revision"], scope, "越出配置范围", key)
            assert api.patch(PROMPT_API, headers=AUTH, json=attempt).status_code == 409
        assert current_prompts(api) == saved
        assert event(api, 7654325, 1).json()["status"] == "ignored"
        assert model.calls == bridge.sent == []


def test_prompt_editor_is_unavailable_in_web_mode(tmp_path, monkeypatch):
    monkeypatch.setenv("QECHO_ADMIN_TOKEN", ADMIN)
    with TestClient(create_app(tmp_path)) as api:
        assert api.get(PROMPT_API, headers=AUTH).status_code in (404, 409)
        body = prompt_edit(1, "global", "文件模式的提示词编辑")
        assert api.patch(PROMPT_API, headers=AUTH, json=body).status_code in (404, 409)


def test_group_and_individual_prompts_reach_only_the_matching_friend(tmp_path):
    cfg = config(tmp_path, prompt_sections=PROMPT_GROUPS)
    allowed = ("7654321", "7654322", "7654323", "7654324")
    model, bridge = Model(), Bridge(friend_ids=(*allowed, "7654325"))
    with TestClient(create_app(model_client=model, bridge_client=bridge, file_config=cfg)) as api:
        state = status_until(api, "running")
        assert state["configured_ids"] == list(allowed)
        assert set(state["selected_ids"]) == set(allowed)
        assert state["friend_prompt_profiles"] == {
            "7654321": {"group": None, "has_personal_prompt": False},
            "7654322": {"group": "亲近好友", "has_personal_prompt": True},
            "7654323": {"group": "亲近好友", "has_personal_prompt": False},
            "7654324": {"group": "同学", "has_personal_prompt": False},
        }
        assert "只有这位好友用昵称小林。" not in json.dumps(state, ensure_ascii=False)
        for message_id, qq_id in enumerate(allowed, start=1):
            assert event(api, int(qq_id), message_id).json()["status"] == "accepted"
        assert event(api, 7654325, 5).json()["status"] == "ignored"
        assert [qq_id for qq_id, _ in bridge.sent] == list(allowed)

        system = [messages[0]["content"] for messages, _ in model.calls]
        group, other, personal = (
            "这一组使用轻松语气。",
            "这一组用简洁语气。",
            "只有这位好友用昵称小林。",
        )
        assert all(cfg.agent.prompt in prompt for prompt in system)
        assert all(fragment not in system[0] for fragment in (group, other, personal))
        assert (
            system[1].index(cfg.agent.prompt) < system[1].index(group) < system[1].index(personal)
        )
        assert other not in system[1]
        assert group in system[2] and other not in system[2] and personal not in system[2]
        assert other in system[3] and group not in system[3] and personal not in system[3]
        contacts = api.get("/api/contacts", headers=AUTH).json()
        contact = next(item for item in contacts if item["qq_id"] == "7654322")
        preview = api.get(f"/api/contacts/{contact['id']}/prompt", headers=AUTH).json()
        assert preview["messages"][0]["content"] == system[1]


def test_group_only_start_and_web_reopen_without_rules_stays_paused(tmp_path, monkeypatch):
    cfg = config(tmp_path, default_ids=(), prompt_sections=PROMPT_GROUPS)
    allowed = ("7654322", "7654323", "7654324")
    bridge = Bridge(friend_ids=allowed)
    with TestClient(create_app(bridge_client=bridge, file_config=cfg)) as api:
        state = status_until(api, "running")
        assert state["configured_ids"] == list(allowed)
        assert set(state["selected_ids"]) == set(allowed)

    monkeypatch.setenv("QECHO_ADMIN_TOKEN", ADMIN)
    monkeypatch.setenv("QECHO_WEBHOOK_TOKEN", EVENT)
    model = Model()
    with TestClient(create_app(cfg.data_directory, model, bridge)) as api:
        state = status_until(api, "manual")
        assert not state["running"]
        assert event(api, 7654322, 1).json()["status"] == "ignored"
        assert not model.calls and not bridge.sent
        result = api.post(
            "/api/automation/start",
            headers=AUTH,
            json={
                "qq_ids": ["7654322"],
                "prompt": "新的网页规则",
                "expected_revision": state["revision"],
            },
        )
        assert result.status_code == 200 and result.json()["running"]
        assert event(api, 7654322, 2).json()["status"] == "accepted"
        assert "新的网页规则" in model.calls[0][0][0]["content"]
        assert "只有这位好友用昵称小林。" not in model.calls[0][0][0]["content"]


def test_unavailable_group_member_blocks_entire_file_start(tmp_path):
    cfg = config(tmp_path, prompt_sections=PROMPT_GROUPS)
    bridge = Bridge(friend_ids=("7654321", "7654322", "7654323"))
    with TestClient(create_app(bridge_client=bridge, file_config=cfg)) as api:
        state = status_until(api, "failed")
        assert not state["running"] and state["selected_ids"] == []
        assert event(api, 7654321, 1).json()["status"] == "ignored"


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
