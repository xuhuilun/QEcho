import asyncio
from types import SimpleNamespace

import pytest

from qecho.adapters import BridgeError
from qecho.engine import Engine
from qecho.simple import SimpleService
from qecho.startup import ConfiguredAutomation
from qecho.store import Store


class StartupBridge:
    def __init__(self):
        self.account = "123456"
        self.entries = [{"qq_id": "234567", "name": "文件指定好友"}]
        self.login_calls = 0
        self.failures = []
        self.fail_on_calls = {}
        self.block_call = None
        self.blocked = asyncio.Event()
        self.release = asyncio.Event()
        self.suppress_cancellation = False

    async def login_info(self, base_url, token):
        self.login_calls += 1
        if self.failures:
            raise self.failures.pop(0)
        if self.login_calls == self.block_call:
            self.blocked.set()
            try:
                await self.release.wait()
            except asyncio.CancelledError:
                if not self.suppress_cancellation:
                    raise
                await self.release.wait()
        if self.login_calls in self.fail_on_calls:
            raise self.fail_on_calls[self.login_calls]
        return self.account

    async def friends(self, base_url, token):
        return self.entries


class ObservedService(SimpleService):
    def __init__(self, store, bridge):
        super().__init__(store, bridge)
        self.sync_calls = 0
        self.start_calls = 0

    async def sync(self):
        self.sync_calls += 1
        return await super().sync()

    async def start(self, qq_ids, prompt, expected_revision, *, keep_current_prompt=False):
        self.start_calls += 1
        return await super().start(
            qq_ids, prompt, expected_revision, keep_current_prompt=keep_current_prompt
        )


def config(auto_start=True, friends=None):
    return SimpleNamespace(
        agent=SimpleNamespace(
            auto_start=auto_start,
            friends=[],
            configured_friends=friends if friends is not None else ["234567"],
            prompt="严格使用文件中的聊天规则",
        )
    )


@pytest.fixture
def setup(tmp_path):
    store = Store(tmp_path / "startup.sqlite3")
    store.update_settings(
        {
            "model_mode": "live",
            "model_base_url": "https://provider.example/v1",
            "model_name": "chat",
            "global_prompt": "严格使用文件中的聊天规则",
            "reply_delay_seconds": 0,
        }
    )
    bridge = StartupBridge()
    service = ObservedService(store, bridge)
    yield store, bridge, service
    store.close()


@pytest.mark.asyncio
async def test_auto_start_false_does_not_schedule_or_contact_bridge(setup):
    _, bridge, service = setup
    startup = ConfiguredAutomation(service, config(auto_start=False))
    assert startup.begin() is None
    assert startup.state == "disabled"
    assert bridge.login_calls == service.start_calls == service.sync_calls == 0
    await startup.shutdown()
    assert startup.state == "disabled"


@pytest.mark.asyncio
async def test_success_uses_file_scope_binds_account_and_runs_only_once(setup):
    _, bridge, service = setup
    startup = ConfiguredAutomation(service, config())
    task = startup.begin()
    assert task is startup.begin()
    assert bridge.login_calls == 0  # begin() itself performs no bridge I/O.
    await asyncio.wait_for(task, 1)
    status = service.status()
    assert startup.state == "running" and startup.error == ""
    assert status["running"] and status["account_id"] == "123456"
    assert status["selected_ids"] == ["234567"]
    assert status["prompt"] == "严格使用文件中的聊天规则"
    service.stop()
    await startup.sync()
    assert startup.begin() is task
    assert service.start_calls == 1
    assert not service.status()["running"]
    await startup.shutdown()


@pytest.mark.asyncio
async def test_transient_bridge_failures_retry_without_disclosing_exception(setup, caplog):
    _, bridge, service = setup
    secret = "model-key-must-stay-private"
    bridge.failures = [BridgeError(secret, retryable=True), BridgeError(secret, retryable=True)]
    startup = ConfiguredAutomation(service, config(), retry_seconds=0.001)
    task = startup.begin()
    await asyncio.wait_for(task, 1)
    assert startup.state == "running" and startup.error == ""
    assert service.sync_calls == 3 and service.start_calls == 1
    assert secret not in startup.error
    assert not [record for record in caplog.records if record.name == "qecho"]
    await startup.shutdown()


@pytest.mark.asyncio
async def test_waiting_error_is_sanitized_and_stop_prevents_any_retry(setup):
    _, bridge, service = setup
    bridge.failures = [BridgeError("api_key=secret&private_message=hidden", retryable=True)]
    startup = ConfiguredAutomation(service, config(), retry_seconds=30)
    task = startup.begin()
    # The bridge and service methods complete without yielding on this failure;
    # one event-loop turn reaches the retry sleep deterministically.
    await asyncio.sleep(0)
    assert startup.state == "waiting"
    assert "secret" not in startup.error and "hidden" not in startup.error
    assert "等待" in startup.error
    stopped = startup.stop()
    assert not stopped["running"] and startup.state == "stopped"
    assert startup.error == "" and startup.begin() is None
    with pytest.raises(asyncio.CancelledError):
        await task
    await startup.sync()
    assert service.sync_calls == 2 and service.start_calls == 0
    assert not service.status()["running"]
    await startup.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("block_call", [1, 3])
@pytest.mark.parametrize("suppress_cancellation", [False, True])
async def test_stop_during_sync_or_start_commits_pause_without_waiting_for_bridge(
    setup, block_call, suppress_cancellation
):
    store, bridge, service = setup
    bridge.block_call = block_call
    bridge.suppress_cancellation = suppress_cancellation
    startup = ConfiguredAutomation(service, config())
    task = startup.begin()
    await asyncio.wait_for(bridge.blocked.wait(), 1)
    before = store.get_settings()["revision"]
    stopped = startup.stop()  # Synchronous: no lock or network await is possible.
    assert not stopped["running"] and stopped["revision"] > before
    assert startup.state == "stopped"
    bridge.release.set()
    try:
        await asyncio.wait_for(task, 1)
    except asyncio.CancelledError:
        pass
    assert not service.status()["running"]
    assert startup.state == "stopped" and startup.error == ""
    assert service.start_calls == (1 if block_call == 3 else 0)
    assert startup.begin() is None
    await startup.shutdown()


@pytest.mark.asyncio
async def test_manual_sync_waits_until_startup_sync_and_start_finish(setup):
    _, bridge, service = setup
    bridge.block_call = 3  # The first account query inside start().
    startup = ConfiguredAutomation(service, config())
    task = startup.begin()
    await asyncio.wait_for(bridge.blocked.wait(), 1)
    manual = asyncio.create_task(startup.sync())
    await asyncio.sleep(0)
    assert service.sync_calls == 1 and not manual.done()
    bridge.release.set()
    await asyncio.wait_for(task, 1)
    result = await asyncio.wait_for(manual, 1)
    assert result["running"] and service.sync_calls == 2
    assert service.start_calls == 1
    await startup.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [ValueError("token=secret"), RuntimeError("key=secret")])
async def test_non_transient_failure_is_terminal_sanitized_and_paused(setup, failure):
    store, bridge, service = setup
    store.update_settings({"enabled": True})
    bridge.failures = [failure]
    startup = ConfiguredAutomation(service, config(), retry_seconds=0.001)
    task = startup.begin()
    await asyncio.wait_for(task, 1)
    assert startup.state == "failed"
    assert "secret" not in startup.error
    assert not service.status()["running"]
    assert bridge.login_calls == 1 and service.start_calls == 0
    assert startup.begin() is task
    await startup.shutdown()


@pytest.mark.asyncio
async def test_invalid_file_friend_range_fails_without_retry_or_authorizing_anyone(setup):
    _, _, service = setup
    startup = ConfiguredAutomation(service, config(friends=["999999"]), retry_seconds=0.001)
    await asyncio.wait_for(startup.begin(), 1)
    assert startup.state == "failed"
    assert not service.status()["running"] and service.status()["selected_ids"] == []
    assert service.sync_calls == service.start_calls == 1
    await startup.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure", ["HTTP 401 api_key=secret", "HTTP 403 token=secret", "bad-url secret"]
)
async def test_permanent_bridge_error_stops_without_retry_or_exposing_details(
    setup, failure, caplog
):
    store, bridge, service = setup
    store.update_settings({"enabled": True})
    bridge.failures = [BridgeError(failure)]
    startup = ConfiguredAutomation(service, config(), retry_seconds=0.001)
    task = startup.begin()
    await asyncio.wait_for(task, 1)
    assert startup.state == "failed"
    assert "桥接配置" in startup.error and "secret" not in startup.error
    assert "401" not in startup.error and "403" not in startup.error
    assert not service.status()["running"]
    assert bridge.login_calls == service.sync_calls == 1 and service.start_calls == 0
    assert startup.begin() is task
    warnings = [record for record in caplog.records if record.name == "qecho"]
    assert len(warnings) == 1 and warnings[0].getMessage() == startup.error
    assert "secret" not in caplog.text
    await startup.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("retryable", [True, False])
async def test_start_phase_bridge_failure_only_retries_when_marked_transient(setup, retryable):
    _, bridge, service = setup
    # sync() uses two account queries; the third occurs after start() pauses.
    bridge.fail_on_calls[3] = BridgeError("private-token=secret", retryable=retryable)
    startup = ConfiguredAutomation(service, config(), retry_seconds=0.001)
    await asyncio.wait_for(startup.begin(), 1)
    assert service.sync_calls == service.start_calls == (2 if retryable else 1)
    assert service.status()["running"] is retryable
    assert startup.state == ("running" if retryable else "failed")
    assert "secret" not in startup.error
    await startup.shutdown()


@pytest.mark.asyncio
async def test_shutdown_drains_startup_before_dependencies_can_close(setup):
    _, bridge, service = setup
    bridge.block_call = 1
    startup = ConfiguredAutomation(service, config())
    task = startup.begin()
    await asyncio.wait_for(bridge.blocked.wait(), 1)
    await asyncio.wait_for(startup.shutdown(), 1)
    assert task.done() and task.cancelled()
    assert startup.begin() is None
    with pytest.raises(ValueError, match="关闭"):
        await startup.sync()
    assert service.start_calls == 0


@pytest.mark.asyncio
async def test_stop_before_begin_cannot_be_rearmed(setup):
    _, bridge, service = setup
    startup = ConfiguredAutomation(service, config())
    startup.stop()
    assert startup.begin() is None
    assert startup.state == "stopped" and bridge.login_calls == 0
    await startup.shutdown()


def existing_contact(store, *, bound=True):
    if bound:
        store.update_settings({"qq_self_id": "123456"})
    return store.create_contact({"qq_id": "234567", "name": "已有联系人", "enabled": False})


def take_over(store, bridge):
    result = Engine(store, None, bridge).ingest_event(
        {
            "self_id": 123456,
            "post_type": "message_sent",
            "message_type": "private",
            "sub_type": "friend",
            "user_id": 123456,
            "target_id": 234567,
            "message_id": 90001,
            "message": "这条我自己回复",
        }
    )
    assert result["status"] == "takeover"


@pytest.mark.asyncio
@pytest.mark.parametrize("block_call", [1, 2, 3])
async def test_takeover_during_sync_or_start_failure_cannot_be_erased_by_retry(
    setup, block_call, caplog
):
    store, bridge, service = setup
    contact = existing_contact(store)
    bridge.block_call = block_call
    bridge.fail_on_calls[block_call] = BridgeError("temporary private-token", retryable=True)
    startup = ConfiguredAutomation(service, config(), retry_seconds=0.001)
    task = startup.begin()
    await asyncio.wait_for(bridge.blocked.wait(), 1)
    take_over(store, bridge)
    bridge.release.set()
    await asyncio.wait_for(task, 1)
    assert startup.state == "failed" and not service.status()["running"]
    assert store.get_contact(contact["id"])["paused"]
    assert bridge.login_calls == block_call  # No new sync may refresh the owner barrier.
    assert service.start_calls == (1 if block_call == 3 else 0)
    assert len([record for record in caplog.records if record.name == "qecho"]) == 1
    assert "private-token" not in caplog.text
    await startup.shutdown()


@pytest.mark.asyncio
async def test_takeover_during_retry_delay_is_checked_before_next_network_action(
    setup, monkeypatch
):
    store, bridge, service = setup
    contact = existing_contact(store)
    bridge.fail_on_calls[3] = BridgeError("temporarily offline", retryable=True)
    sleeping, release = asyncio.Event(), asyncio.Event()
    real_sleep = asyncio.sleep

    async def controlled_sleep(delay):
        if delay == 3:
            sleeping.set()
            await release.wait()
        else:
            await real_sleep(delay)

    # Hold the retry boundary without a timing-dependent wall-clock sleep.
    monkeypatch.setattr("qecho.startup.asyncio.sleep", controlled_sleep)
    startup = ConfiguredAutomation(service, config())
    task = startup.begin()
    await asyncio.wait_for(sleeping.wait(), 1)
    take_over(store, bridge)
    release.set()
    await asyncio.wait_for(task, 1)
    assert startup.state == "failed" and not service.status()["running"]
    assert store.get_contact(contact["id"])["paused"]
    assert bridge.login_calls == 3 and service.sync_calls == service.start_calls == 1
    await startup.shutdown()


@pytest.mark.asyncio
async def test_first_binding_revision_increment_is_allowed_and_later_takeover_is_not_global_failure(
    setup,
):
    store, bridge, service = setup
    contact = existing_contact(store, bound=False)
    startup = ConfiguredAutomation(service, config())
    await asyncio.wait_for(startup.begin(), 1)
    assert startup.state == "running" and service.status()["running"]
    # One legitimate bind increment and one successful activation increment.
    assert store.get_contact(contact["id"])["revision"] == contact["revision"] + 2
    take_over(store, bridge)
    status = await startup.sync()
    assert status["running"] and status["paused_ids"] == ["234567"]
    assert startup.state == "running" and startup.error == ""
    assert service.start_calls == 1
    await startup.shutdown()
