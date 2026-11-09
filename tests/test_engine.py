import asyncio

import pytest

from qecho.adapters import BridgeError, ProviderError
from qecho.engine import Engine
from qecho.store import Store


class FakeModel:
    def __init__(self):
        self.calls = []
        self.started = asyncio.Event()
        self.release = None
        self.failure = None

    async def generate(self, settings, messages, api_key):
        self.calls.append(messages)
        self.started.set()
        if self.release:
            await self.release.wait()
        if self.failure:
            raise self.failure
        return "好呀，慢慢聊。"


class FakeBridge:
    def __init__(self):
        self.calls = []
        self.failure = None

    async def login_info(self, base_url, token):
        return "123456"

    async def send_private(self, base_url, token, user_id, text):
        self.calls.append((user_id, text))
        if self.failure:
            raise self.failure
        return str(1000 + len(self.calls))


@pytest.fixture
def core(tmp_path):
    store = Store(tmp_path / "qecho.db")
    store.update_settings(
        {"enabled": True, "qq_self_id": "123456", "model_mode": "live", "reply_delay_seconds": 0}
    )
    contact = store.create_contact(
        {
            "qq_id": "234567",
            "name": "小满",
            "enabled": True,
            "persona": "真诚、有幽默感",
            "tone": "轻松，少用表情",
            "relationship": "有好感的朋友",
            "instructions": "不要连续追问",
            "facts": "主人喜欢摄影",
        }
    )
    model, bridge = FakeModel(), FakeBridge()
    engine = Engine(store, model, bridge)
    yield store, contact, engine, model, bridge
    store.close()


def event(message_id=1, text="今天怎么样？", **patch):
    return {
        "self_id": 123456,
        "post_type": "message",
        "message_type": "private",
        "sub_type": "friend",
        "user_id": 234567,
        "message_id": message_id,
        "message": [{"type": "text", "data": {"text": text}}],
    } | patch


async def draft(engine, payload=None):
    accepted = engine.ingest_event(payload or event())
    assert accepted["status"] == "accepted"
    return await engine.process_message(accepted["message_id"])


def test_default_permissions_and_secret_write_only(tmp_path):
    store = Store(tmp_path / "default.db")
    assert store.get_settings()["enabled"] is False
    assert store.get_settings()["model_mode"] == "demo"
    contact = store.create_contact({"qq_id": "123456", "name": "朋友"})
    assert not contact["enabled"] and contact["mode"] == "draft"
    settings = store.update_settings({"model_api_key": "secret", "bridge_token": "token"})
    assert settings["model_api_key_set"] and "model_api_key" not in settings
    store.update_settings({"model_api_key": ""})
    assert store.get_secret("model_api_key") == "secret"
    store.update_settings({"clear_model_api_key": True})
    assert not store.get_settings()["model_api_key_set"]
    store.close()


@pytest.mark.parametrize(
    "patch",
    [
        {"self_id": 999999},
        {"message_type": "group"},
        {"sub_type": "group"},
        {"user_id": 999999},
        {"post_type": "notice"},
        {"message_id": None},
        {"message": [{"type": "image", "data": {"file": "x"}}]},
        {"message": [{"type": "text", "data": "malformed"}]},
        {"message": "[CQ:image,file=x]"},
    ],
)
def test_out_of_scope_events_do_not_persist(core, patch):
    store, contact, engine, model, bridge = core
    assert engine.ingest_event(event(**patch))["status"] == "ignored"
    assert store.list_messages(contact["id"]) == []
    assert not model.calls and not bridge.calls


def test_global_pause_and_contact_whitelist(core):
    store, contact, engine, _, _ = core
    store.update_contact(contact["id"], {"enabled": False})
    assert engine.ingest_event(event())["status"] == "ignored"
    store.update_contact(contact["id"], {"enabled": True})
    store.update_settings({"enabled": False})
    assert engine.ingest_event(event())["status"] == "ignored"
    assert store.list_messages(contact["id"]) == []


@pytest.mark.asyncio
async def test_draft_preview_and_explicit_send_are_idempotent(core):
    store, contact, engine, model, bridge = core
    reply = await draft(engine)
    assert reply["status"] == "draft" and not bridge.calls
    prompt = model.calls[0][0]["content"]
    for expected in (
        "小满",
        "真诚、有幽默感",
        "轻松，少用表情",
        "有好感的朋友",
        "不要连续追问",
        "主人喜欢摄影",
    ):
        assert expected in prompt
    sent = await engine.send_draft(reply["id"], "可以呀，晚点聊。")
    assert sent["status"] == "sent"
    assert await engine.send_draft(reply["id"]) == sent
    assert bridge.calls == [(contact["qq_id"], "可以呀，晚点聊。")]


@pytest.mark.asyncio
async def test_duplicate_events_remain_deduplicated_after_memory_clear(core):
    store, contact, engine, model, _ = core
    await draft(engine)
    assert engine.ingest_event(event())["status"] == "duplicate"
    store.clear_messages(contact["id"])
    assert engine.ingest_event(event())["status"] == "duplicate"
    assert len(model.calls) == 1


@pytest.mark.asyncio
async def test_simulator_never_sends_and_does_not_pollute_live_history(core):
    store, contact, engine, model, bridge = core
    store.update_contact(contact["id"], {"mode": "auto"})
    simulation = await engine.simulate(contact["id"], "仅演练可见的秘密")
    assert simulation["source"] == "simulator" and simulation["status"] == "draft"
    with pytest.raises(ValueError, match="演练"):
        await engine.send_draft(simulation["id"])
    real = await draft(engine)
    assert real["status"] == "sent"
    assert "仅演练可见的秘密" not in str(model.calls[-1])
    assert len(bridge.calls) == 1


@pytest.mark.asyncio
async def test_contact_histories_are_isolated(core):
    store, contact, engine, model, _ = core
    other = store.create_contact({"qq_id": "345678", "name": "另一位", "persona": "另外的人格"})
    await engine.simulate(other["id"], "不能跨联系人传播")
    await draft(engine)
    assert "不能跨联系人传播" not in str(model.calls[-1])
    assert "另外的人格" not in str(model.calls[-1])


@pytest.mark.asyncio
async def test_simulation_works_without_live_permissions(core):
    store, contact, engine, _, bridge = core
    store.update_settings({"enabled": False})
    store.update_contact(contact["id"], {"enabled": False, "paused": True})
    assert (await engine.simulate(contact["id"], "先试试人格"))["status"] == "draft"
    assert not bridge.calls


@pytest.mark.asyncio
async def test_demo_model_can_never_send_real_qq(core):
    store, contact, engine, _, bridge = core
    store.update_settings({"model_mode": "demo"})
    store.update_contact(contact["id"], {"mode": "auto"})
    reply = await draft(engine)
    assert reply["status"] == "draft"
    with pytest.raises(ValueError, match="演练模型"):
        await engine.send_draft(reply["id"])
    assert not bridge.calls


@pytest.mark.asyncio
async def test_auto_send_and_process_replay_do_not_repeat(core):
    store, contact, engine, model, bridge = core
    store.update_contact(contact["id"], {"mode": "auto"})
    receipt = engine.ingest_event(event())
    reply = await engine.process_message(receipt["message_id"])
    assert reply["status"] == "sent"
    await engine.process_message(receipt["message_id"])
    assert len(bridge.calls) == len(model.calls) == 1


@pytest.mark.asyncio
async def test_model_failure_is_visible_and_never_sent(core):
    store, contact, engine, model, bridge = core
    model.failure = ProviderError("模型请求超时")
    reply = await draft(engine)
    assert reply["status"] == "error" and reply["error"] == "模型请求超时"
    assert not bridge.calls


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["contact", "settings", "takeover", "clear", "cancel"])
async def test_inflight_generation_respects_owner_changes(core, change):
    store, contact, engine, model, bridge = core
    model.release = asyncio.Event()
    receipt = engine.ingest_event(event())
    task = asyncio.create_task(engine.process_message(receipt["message_id"]))
    await model.started.wait()
    if change == "contact":
        store.update_contact(contact["id"], {"persona": "新的设定"})
    elif change == "settings":
        store.update_settings({"enabled": False})
    elif change == "takeover":
        takeover = event(
            77, "我自己来聊", post_type="message_sent", user_id=123456, target_id=234567
        )
        assert engine.ingest_event(takeover)["status"] == "takeover"
        assert store.get_contact(contact["id"])["paused"]
    elif change == "clear":
        store.clear_messages(contact["id"])
    else:
        outgoing = store.list_messages(contact["id"])[-1]
        engine.cancel_draft(outgoing["id"])
    model.release.set()
    reply = await task
    assert reply["status"] == "cancelled"
    assert not bridge.calls


@pytest.mark.asyncio
async def test_pause_during_reply_delay_prevents_send(core):
    store, contact, engine, model, bridge = core
    store.update_contact(contact["id"], {"mode": "auto"})
    store.update_settings({"reply_delay_seconds": 0.02})
    receipt = engine.ingest_event(event())
    task = asyncio.create_task(engine.process_message(receipt["message_id"]))
    await model.started.wait()
    store.update_contact(contact["id"], {"paused": True})
    reply = await task
    assert reply["status"] == "cancelled" and not bridge.calls


@pytest.mark.asyncio
async def test_queued_messages_include_prior_sent_reply_in_order(core):
    store, contact, engine, model, bridge = core
    store.update_contact(contact["id"], {"mode": "auto"})
    first, second = engine.ingest_event(event(1, "第一句")), engine.ingest_event(event(2, "第二句"))
    await asyncio.gather(
        engine.process_message(first["message_id"]), engine.process_message(second["message_id"])
    )
    assert [m["content"] for m in model.calls[0][1:]] == ["第一句"]
    assert [m["role"] for m in model.calls[1][1:]] == ["user", "assistant", "user"]
    assert len(bridge.calls) == 2


@pytest.mark.asyncio
async def test_unknown_send_is_not_retried(core):
    _, _, engine, _, bridge = core
    reply = await draft(engine)
    bridge.failure = BridgeError("响应丢失", uncertain=True)
    failed = await engine.send_draft(reply["id"])
    assert failed["status"] == "unknown"
    with pytest.raises(ValueError, match="未知"):
        await engine.send_draft(reply["id"])
    assert len(bridge.calls) == 1


@pytest.mark.asyncio
async def test_self_echo_does_not_trigger_takeover(core):
    store, contact, engine, _, _ = core
    reply = await draft(engine)
    sent = await engine.send_draft(reply["id"])
    echo = event(
        sent["external_id"],
        sent["content"],
        post_type="message_sent",
        user_id=123456,
        target_id=234567,
    )
    assert engine.ingest_event(echo)["reason"] == "own_reply"
    assert not store.get_contact(contact["id"])["paused"]


@pytest.mark.asyncio
async def test_echo_before_send_response_cannot_prove_delivery_after_timeout(core):
    store, contact, engine, _, bridge = core
    reply = await draft(engine)

    async def echo_then_timeout(base_url, token, user_id, text):
        echo = event(777, text, post_type="message_sent", user_id=123456, target_id=int(user_id))
        assert engine.ingest_event(echo)["status"] == "pending_self_event"
        raise BridgeError("响应丢失", uncertain=True)

    bridge.send_private = echo_then_timeout
    result = await engine.send_draft(reply["id"])
    assert result["status"] == "unknown" and result["external_id"] is None
    assert store.get_contact(contact["id"])["paused"]


@pytest.mark.asyncio
async def test_early_echo_is_confirmed_only_by_returned_message_id(core):
    store, contact, engine, _, bridge = core
    reply = await draft(engine)

    async def echo_then_success(base_url, token, user_id, text):
        echo = event(777, text, post_type="message_sent", user_id=123456, target_id=int(user_id))
        assert engine.ingest_event(echo)["status"] == "pending_self_event"
        return "777"

    bridge.send_private = echo_then_success
    result = await engine.send_draft(reply["id"])
    assert result["status"] == "sent" and result["external_id"] == "777"
    assert not store.get_contact(contact["id"])["paused"]
    assert not store.list_self_events()


@pytest.mark.asyncio
async def test_human_typing_identical_text_during_send_still_takes_over(core):
    store, contact, engine, _, bridge = core
    reply = await draft(engine)

    async def human_then_echo(base_url, token, user_id, text):
        human = event(999, text, post_type="message_sent", user_id=123456, target_id=int(user_id))
        echo = event(1000, text, post_type="message_sent", user_id=123456, target_id=int(user_id))
        assert engine.ingest_event(human)["status"] == "pending_self_event"
        assert engine.ingest_event(echo)["status"] == "pending_self_event"
        return "1000"

    bridge.send_private = human_then_echo
    result = await engine.send_draft(reply["id"])
    assert result["status"] == "sent" and result["external_id"] == "1000"
    assert store.get_contact(contact["id"])["paused"]
    assert any(item["external_id"] == "999" for item in store.list_messages(contact["id"]))
    assert not store.list_self_events()


@pytest.mark.asyncio
async def test_stale_or_cancelled_drafts_cannot_send(core):
    store, contact, engine, _, bridge = core
    reply = await draft(engine)
    store.update_contact(contact["id"], {"tone": "新语气"})
    with pytest.raises(ValueError):
        await engine.send_draft(reply["id"])
    assert not bridge.calls


@pytest.mark.asyncio
async def test_account_and_contact_identity_changes_cannot_mix_history(core):
    store, contact, engine, _, _ = core
    await draft(engine)
    with pytest.raises(ValueError, match="清理"):
        store.update_settings({"qq_self_id": "999999"})
    with pytest.raises(ValueError, match="不可更改"):
        store.update_contact(contact["id"], {"qq_id": "999999"})
    store.clear_messages(contact["id"])
    assert store.update_settings({"qq_self_id": "999999"})["qq_self_id"] == "999999"


def test_restart_marks_inflight_sends_unknown_and_does_not_replay(tmp_path):
    path = tmp_path / "restart.db"
    store = Store(path)
    contact = store.create_contact({"qq_id": "234567", "name": "朋友"})
    sent = store.add_message(contact["id"], "outgoing", "可能已送达", "qq", "sending")
    pending = store.add_message(contact["id"], "incoming", "待处理", "qq", "received")
    store.queue_self_event(contact["id"], "999", "可能是主人手发")
    store.close()
    store = Store(path)
    assert store.get_message(sent["id"])["status"] == "unknown"
    assert store.get_message(pending["id"])["status"] == "cancelled"
    Engine(store, FakeModel(), FakeBridge())
    assert store.get_contact(contact["id"])["paused"]
    assert not store.list_self_events()
    store.close()


@pytest.mark.asyncio
async def test_regenerate_uses_latest_rules_and_always_stays_draft(core):
    store, contact, engine, model, bridge = core
    reply = await draft(engine)
    store.update_contact(contact["id"], {"persona": "换一个人格", "mode": "auto"})
    regenerated = await engine.regenerate(reply["id"])
    assert regenerated["status"] == "draft"
    assert regenerated["id"] != reply["id"]
    assert "换一个人格" in model.calls[-1][0]["content"]
    assert store.get_message(reply["id"])["status"] == "cancelled"
    assert not bridge.calls


@pytest.mark.asyncio
async def test_regenerate_cannot_bypass_paused_permissions_or_simulator(core):
    store, contact, engine, _, bridge = core
    reply = await draft(engine)
    simulation = await engine.simulate(contact["id"], "演练")
    with pytest.raises(ValueError, match="演练"):
        await engine.regenerate(simulation["id"])
    store.update_contact(contact["id"], {"paused": True})
    with pytest.raises(ValueError, match="接管"):
        await engine.regenerate(reply["id"])
    assert not bridge.calls


@pytest.mark.asyncio
async def test_regenerate_does_not_reply_to_older_incoming_message(core):
    _, _, engine, _, _ = core
    old = await draft(engine)
    await draft(engine, event(2, "新的问题"))
    with pytest.raises(ValueError, match="最后一条"):
        await engine.regenerate(old["id"])


@pytest.mark.asyncio
async def test_regenerate_cancels_inflight_generation_before_it_can_auto_send(core):
    store, contact, engine, model, bridge = core
    store.update_contact(contact["id"], {"mode": "auto"})
    model.release = asyncio.Event()
    receipt = engine.ingest_event(event())
    original = asyncio.create_task(engine.process_message(receipt["message_id"]))
    await model.started.wait()
    regenerated = asyncio.create_task(engine.regenerate(receipt["message_id"]))
    await asyncio.sleep(0)
    model.release.set()
    original_result, regenerated_result = await asyncio.gather(original, regenerated)
    assert original_result["status"] == "cancelled"
    assert regenerated_result["status"] == "draft"
    assert not bridge.calls


@pytest.mark.asyncio
async def test_unknown_delivery_cannot_be_regenerated_as_implicit_retry(core):
    _, _, engine, _, bridge = core
    reply = await draft(engine)
    bridge.failure = BridgeError("未收到响应", uncertain=True)
    await engine.send_draft(reply["id"])
    with pytest.raises(ValueError, match="人工核对"):
        await engine.regenerate(reply["id"])


@pytest.mark.asyncio
async def test_actual_bridge_account_must_match_configured_owner_before_send(core):
    _, _, engine, _, bridge = core
    reply = await draft(engine)

    async def switched_account(base_url, token):
        return "999999"

    bridge.login_info = switched_account
    result = await engine.send_draft(reply["id"])
    assert result["status"] == "error" and "账号不一致" in result["error"]
    assert not bridge.calls


@pytest.mark.asyncio
async def test_identity_query_failure_is_definite_no_send(core):
    _, _, engine, _, bridge = core
    reply = await draft(engine)

    async def failed_account_query(base_url, token):
        raise BridgeError("账号查询超时", uncertain=True)

    bridge.login_info = failed_account_query
    result = await engine.send_draft(reply["id"])
    assert result["status"] == "error" and not bridge.calls


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["pause", "cancel", "settings"])
@pytest.mark.parametrize("failure", [False, True])
async def test_owner_changes_during_account_query_keep_reply_cancelled(core, change, failure):
    store, contact, engine, _, bridge = core
    reply = await draft(engine)
    started, release = asyncio.Event(), asyncio.Event()

    async def delayed_query(base_url, token):
        started.set()
        await release.wait()
        if failure:
            raise BridgeError("账号查询失败")
        return "123456"

    bridge.login_info = delayed_query
    sending = asyncio.create_task(engine.send_draft(reply["id"]))
    await started.wait()
    if change == "pause":
        store.update_contact(contact["id"], {"paused": True})
    elif change == "cancel":
        engine.cancel_draft(reply["id"])
    else:
        store.update_settings({"enabled": False})
    release.set()
    assert (await sending)["status"] == "cancelled"
    assert not bridge.calls


@pytest.mark.asyncio
async def test_missing_owner_identity_cannot_send_even_directly_created_draft(core):
    store, contact, engine, _, bridge = core
    store.update_settings({"qq_self_id": ""})
    reply = store.add_message(contact["id"], "outgoing", "测试草稿", "qq", "draft")
    result = await engine.send_draft(reply["id"])
    assert result["status"] == "error" and "本人 QQ" in result["error"]
    assert not bridge.calls


@pytest.mark.asyncio
async def test_background_task_start_order_cannot_reorder_durable_events(core):
    store, contact, engine, model, bridge = core
    store.update_contact(contact["id"], {"mode": "auto"})

    async def echo_latest(settings, messages, api_key):
        model.calls.append(messages)
        return "回复" + messages[-1]["content"]

    model.generate = echo_latest
    first = engine.ingest_event(event(1, "第一条"))
    second = engine.ingest_event(event(2, "第二条"))
    await engine.process_message(second["message_id"])
    await engine.process_message(first["message_id"])
    assert [text for _, text in bridge.calls] == ["回复第一条", "回复第二条"]
    assert len(model.calls) == 2
