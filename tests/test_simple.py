import asyncio
import json

import pytest
from pydantic import ValidationError

from qecho.adapters import BridgeError, ProviderError
from qecho.engine import Engine
from qecho.schemas import AutomationStartInput, SettingsPatch
from qecho.simple import SimpleService
from qecho.store import Store


class LocalBridge:
    def __init__(self):
        self.account = "123456"
        self.entries = [
            {"qq_id": "234567", "name": "好友甲"},
            {"qq_id": "345678", "name": "好友乙"},
        ]
        self.sent = []
        self.failure = None
        self.friends_started = asyncio.Event()
        self.friends_release = None

    async def login_info(self, base_url, token):
        if self.failure:
            raise self.failure
        return self.account

    async def friends(self, base_url, token):
        self.friends_started.set()
        if self.friends_release:
            await self.friends_release.wait()
        return self.entries

    async def send_private(self, base_url, token, user_id, text):
        self.sent.append((user_id, text))
        return str(len(self.sent))


class LocalModel:
    def __init__(self):
        self.messages = []
        self.started = asyncio.Event()
        self.release = None
        self.failure = None

    async def generate(self, settings, messages, key):
        self.messages.append(messages)
        self.started.set()
        if self.release:
            await self.release.wait()
        if self.failure:
            raise self.failure
        return "简短、自然的统一回复"


@pytest.fixture
def setup(tmp_path):
    store = Store(tmp_path / "simple.db")
    store.update_settings(
        {
            "model_mode": "live",
            "model_base_url": "https://provider.example/v1",
            "model_name": "chat",
            "qq_self_id": "123456",
            "reply_delay_seconds": 0,
        }
    )
    bridge = LocalBridge()
    service = SimpleService(store, bridge)
    yield store, bridge, service
    store.close()


async def start(service, qq_ids=None, prompt="按统一提示词自然聊天"):
    return await service.start(qq_ids or ["234567"], prompt, service.status()["revision"])


def event(message_id=1, user_id=234567):
    return {
        "self_id": 123456,
        "post_type": "message",
        "message_type": "private",
        "sub_type": "friend",
        "user_id": user_id,
        "message_id": message_id,
        "message": "今天开心吗",
    }


@pytest.mark.parametrize(
    "patch",
    [
        {"qq_ids": []},
        {"qq_ids": ["234567", "234567"]},
        {"qq_ids": ["bad"]},
        {"qq_ids": [str(100000 + i) for i in range(501)]},
        {"prompt": "  "},
        {"prompt": "字" * 20001},
        {"expected_revision": 0},
        {"expected_revision": True},
    ],
)
def test_start_input_rejects_invalid_scope_and_prompt(patch):
    body = {"qq_ids": ["234567"], "prompt": "自然聊天", "expected_revision": 1} | patch
    with pytest.raises(ValidationError):
        AutomationStartInput(**body)


@pytest.mark.parametrize(
    "url", ["javascript:alert(1)", "file:///tmp/login", "http://", "https://bad url/login"]
)
def test_login_page_only_accepts_valid_http_urls(url):
    with pytest.raises(ValidationError):
        SettingsPatch(bridge_login_url=url)


def test_legacy_defaults_and_login_url_are_compatible(setup):
    store, _, service = setup
    assert store.get_settings()["simple_mode"] is False
    assert service.status()["login_url"] == "http://127.0.0.1:6099/webui/qq_login"
    store.update_settings({"bridge_login_url": "https://bridge.example/login"})
    assert service.status()["login_url"] == "https://bridge.example/login"


@pytest.mark.asyncio
async def test_sync_binds_actual_account_and_does_not_authorize_friends(setup):
    store, bridge, service = setup
    store.update_settings({"qq_self_id": ""})
    result = await service.sync()
    assert result["account_id"] == bridge.account
    assert result["friends"] == bridge.entries
    assert result["selected_ids"] == [] and not result["running"]
    assert store.list_contacts() == []


@pytest.mark.asyncio
async def test_start_replaces_complete_scope_and_simple_prompt_ignores_old_personality(setup):
    store, bridge, service = setup
    old = store.create_contact(
        {
            "qq_id": "234567",
            "name": "旧称呼",
            "enabled": True,
            "mode": "draft",
            "paused": True,
            "persona": "隐藏旧人格",
            "tone": "隐藏旧语气",
            "facts": "隐藏旧事实",
            "instructions": "隐藏旧指令",
        }
    )
    removed = store.create_contact(
        {"qq_id": "456789", "name": "取消勾选", "enabled": True, "mode": "auto"}
    )
    draft = store.add_message(old["id"], "outgoing", "旧草稿", "qq", "draft")
    result = await start(service)
    assert result["running"] and result["selected_ids"] == ["234567"]
    assert store.get_contact(old["id"])["mode"] == "auto"
    assert not store.get_contact(old["id"])["paused"]
    assert not store.get_contact(removed["id"])["enabled"]
    assert store.get_message(draft["id"])["status"] == "cancelled"
    assert store.get_contact(old["id"])["persona"] == "隐藏旧人格"
    engine, model = Engine(store, LocalModel(), bridge), LocalModel()
    engine.model = model
    accepted = engine.ingest_event(event())
    assert (await engine.process_message(accepted["message_id"]))["status"] == "sent"
    prompt = model.messages[-1][0]["content"]
    assert "按统一提示词自然聊天" in prompt
    assert "好友甲" in prompt
    for hidden in ("隐藏旧人格", "隐藏旧语气", "隐藏旧事实", "隐藏旧指令"):
        assert hidden not in prompt
    assert engine.ingest_event(event(2, 456789))["status"] == "ignored"
    assert len(bridge.sent) == 1


@pytest.mark.asyncio
async def test_changing_scope_and_prompt_cancels_previous_replies(setup):
    store, _, service = setup
    await start(service)
    original = store.find_contact("234567")
    draft = store.add_message(original["id"], "outgoing", "旧待发", "qq", "draft")
    result = await start(service, ["345678"], "新的统一提示词")
    assert result["selected_ids"] == ["345678"]
    assert result["prompt"] == "新的统一提示词"
    assert not store.get_contact(original["id"])["enabled"]
    assert store.get_message(draft["id"])["status"] == "cancelled"


@pytest.mark.asyncio
async def test_unlisted_qq_cannot_be_added_by_client_and_failure_stays_stopped(setup):
    _, _, service = setup
    await start(service)
    with pytest.raises(ValueError, match="好友列表"):
        await start(service, ["999999"])
    assert not service.status()["running"]
    assert "999999" not in service.status()["selected_ids"]


@pytest.mark.asyncio
async def test_stale_start_does_not_override_more_recent_owner_action(setup):
    _, _, service = setup
    old_revision = service.status()["revision"]
    await start(service)
    with pytest.raises(ValueError, match="配置已变化"):
        await service.start(["345678"], "过期界面提交", old_revision)
    assert service.status()["running"]
    assert service.status()["selected_ids"] == ["234567"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "patch",
    [
        {"model_mode": "demo"},
        {"model_base_url": ""},
        {"model_name": ""},
        {"model_base_url": "file:///tmp/model"},
    ],
)
async def test_bad_model_configuration_stops_before_start_validation(setup, patch):
    store, _, service = setup
    store.update_settings(patch | {"enabled": True})
    with pytest.raises(ValueError, match="真实模型"):
        await start(service)
    assert not service.status()["running"]


@pytest.mark.asyncio
async def test_bridge_failure_after_start_barrier_keeps_global_stopped(setup):
    store, bridge, service = setup
    store.update_settings({"enabled": True})
    bridge.failure = BridgeError("尚未登录 QQ")
    with pytest.raises(BridgeError):
        await start(service)
    assert not service.status()["running"]


@pytest.mark.asyncio
async def test_stop_during_slow_start_cannot_be_undone_by_validation_result(setup):
    _, bridge, service = setup
    bridge.friends_release = asyncio.Event()
    task = asyncio.create_task(start(service))
    await bridge.friends_started.wait()
    stopped = service.stop()
    assert not stopped["running"]
    bridge.friends_release.set()
    with pytest.raises(ValueError, match="配置或停止状态"):
        await task
    assert not service.status()["running"]


@pytest.mark.asyncio
async def test_settings_change_during_start_invalidates_snapshot(setup):
    store, bridge, service = setup
    bridge.friends_release = asyncio.Event()
    task = asyncio.create_task(start(service))
    await bridge.friends_started.wait()
    store.update_settings({"global_prompt": "另一处保存的新规则"})
    bridge.friends_release.set()
    with pytest.raises(ValueError, match="配置或停止状态"):
        await task
    assert not service.status()["running"]
    assert service.status()["prompt"] == "另一处保存的新规则"


@pytest.mark.asyncio
async def test_account_switch_with_real_history_stops_without_rebinding(setup):
    store, bridge, service = setup
    await start(service)
    contact = store.find_contact("234567")
    store.add_message(contact["id"], "incoming", "旧账号真实历史", "qq", "processed")
    bridge.account = "999999"
    with pytest.raises(ValueError, match="独立数据目录"):
        await service.sync()
    assert not service.status()["running"]
    assert service.status()["account_id"] == "123456"


@pytest.mark.asyncio
async def test_account_switch_without_real_history_drops_old_selection(setup):
    store, bridge, service = setup
    await start(service)
    bridge.account = "999999"
    result = await service.sync()
    assert not result["running"]
    assert result["account_id"] == "999999"
    assert result["selected_ids"] == []
    assert store.list_contacts()


@pytest.mark.asyncio
async def test_account_change_while_fetching_friends_requires_resync(setup):
    _, bridge, service = setup
    bridge.friends_release = asyncio.Event()
    task = asyncio.create_task(service.sync())
    await bridge.friends_started.wait()
    bridge.account = "999999"
    bridge.friends_release.set()
    with pytest.raises(ValueError, match="重新同步"):
        await task
    assert not service.status()["running"]


@pytest.mark.asyncio
@pytest.mark.parametrize("model_failure", [False, True])
async def test_stop_during_generation_returns_immediately_and_no_reply_can_send(
    setup, model_failure
):
    store, bridge, service = setup
    await start(service)
    model = LocalModel()
    model.release = asyncio.Event()
    if model_failure:
        model.failure = ProviderError("模型请求失败")
    engine = Engine(store, model, bridge)
    receipt = engine.ingest_event(event())
    processing = asyncio.create_task(engine.process_message(receipt["message_id"]))
    await model.started.wait()
    result = service.stop()
    assert not result["running"] and not processing.done()
    model.release.set()
    assert (await processing)["status"] == "cancelled"
    assert not bridge.sent


@pytest.mark.asyncio
async def test_range_change_during_generation_invalidates_old_reply(setup):
    store, bridge, service = setup
    await start(service)
    model = LocalModel()
    model.release = asyncio.Event()
    engine = Engine(store, model, bridge)
    receipt = engine.ingest_event(event())
    processing = asyncio.create_task(engine.process_message(receipt["message_id"]))
    await model.started.wait()
    await start(service, ["345678"], "改后的提示词")
    model.release.set()
    assert (await processing)["status"] == "cancelled"
    assert engine.ingest_event(event(2, 234567))["status"] == "ignored"
    assert not bridge.sent


def test_atomic_batch_rolls_back_all_contact_updates_on_failure(setup):
    store, _, _ = setup
    old = store.create_contact({"qq_id": "234567", "name": "保留授权", "enabled": True})
    version = store.get_settings()["revision"]
    with pytest.raises(ValidationError):
        store.activate_automation(
            "123456",
            [{"qq_id": "345678", "name": ""}],
            "新提示词",
            version,
            expected_contacts=store.contact_revisions(),
        )
    assert store.get_contact(old["id"])["enabled"]
    assert store.find_contact("345678") is None
    assert store.get_settings()["revision"] == version


def test_status_is_bounded_and_does_not_return_keys_or_message_contents(setup):
    store, _, service = setup
    store.update_settings(
        {"model_api_key": "secret-model-key", "bridge_token": "secret-bridge-key"}
    )
    contact = store.create_contact({"qq_id": "234567", "name": "好友甲"})
    for number in range(12):
        store.add_message(contact["id"], "outgoing", f"私密消息{number}", "qq", "sent")
    store.add_message(contact["id"], "outgoing", "不属于真实QQ的演练", "simulator", "draft")
    result = service.status()
    assert len(result["recent"]) == 8
    assert set(result["recent"][0]) == {"contact_name", "status", "error", "created_at"}
    text = json.dumps(result, ensure_ascii=False)
    assert "secret-" not in text and "私密消息" not in text and "演练" not in text


@pytest.mark.asyncio
async def test_status_distinguishes_selected_contacts_from_manual_takeover(setup):
    store, _, service = setup
    await start(service, ["234567", "345678"])
    store.update_contact(store.find_contact("234567")["id"], {"paused": True})
    result = service.status()
    assert result["running"]
    assert set(result["selected_ids"]) == {"234567", "345678"}
    assert result["paused_ids"] == ["234567"] and result["active_count"] == 1
    resumed = await start(service, ["234567", "345678"])
    assert resumed["paused_ids"] == [] and resumed["active_count"] == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change", ["takeover", "permission", "prompt", "create", "delete", "replace"]
)
async def test_later_contact_control_changes_cannot_be_overwritten_by_slow_start(setup, change):
    store, bridge, service = setup
    await start(service)
    contact = store.find_contact("234567")
    bridge.friends_started.clear()
    bridge.friends_release = asyncio.Event()
    pending_start = asyncio.create_task(start(service))
    await bridge.friends_started.wait()
    if change == "takeover":
        engine = Engine(store, LocalModel(), bridge)
        own_message = event(77) | {
            "post_type": "message_sent",
            "user_id": 123456,
            "target_id": 234567,
            "message": "我自己来聊",
        }
        assert engine.ingest_event(own_message)["status"] == "takeover"
    elif change == "permission":
        store.update_contact(contact["id"], {"enabled": False})
    elif change == "prompt":
        store.update_contact(contact["id"], {"instructions": "更晚保存的联系人指令"})
    elif change == "create":
        store.create_contact({"qq_id": "456789", "name": "刚添加"})
    else:
        store.delete_contact(contact["id"])
        if change == "replace":
            store.create_contact({"qq_id": "234567", "name": "删除后重建"})
    bridge.friends_release.set()
    with pytest.raises(ValueError, match="联系人权限或接管状态"):
        await pending_start
    assert not service.status()["running"]
    if change == "takeover":
        assert store.get_contact(contact["id"])["paused"]
    elif change == "permission":
        assert not store.get_contact(contact["id"])["enabled"]


@pytest.mark.asyncio
async def test_takeover_during_initial_login_query_also_blocks_start(setup):
    store, bridge, service = setup
    await start(service)
    query_started, query_release = asyncio.Event(), asyncio.Event()

    async def slow_login(base_url, token):
        query_started.set()
        await query_release.wait()
        return bridge.account

    bridge.login_info = slow_login
    pending_start = asyncio.create_task(start(service))
    await query_started.wait()
    contact = store.find_contact("234567")
    store.update_contact(contact["id"], {"paused": True})
    query_release.set()
    with pytest.raises(ValueError, match="联系人权限或接管状态"):
        await pending_start
    assert not service.status()["running"]
    assert store.get_contact(contact["id"])["paused"]


@pytest.mark.asyncio
async def test_contact_paused_before_explicit_start_can_still_be_resumed(setup):
    store, _, service = setup
    contact = store.create_contact(
        {"qq_id": "234567", "name": "之前已接管", "enabled": True, "paused": True}
    )
    result = await start(service)
    assert result["running"] and result["paused_ids"] == []
    assert not store.get_contact(contact["id"])["paused"]
