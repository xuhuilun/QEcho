import json
import sqlite3

import pytest

from qecho.config import ConfigError, load_config
from qecho.store import Store

FULL_CONFIG = '''
[server]
host = "0.0.0.0"
port = 9876
data_dir = "local state"
admin_token = "admin-secret-123456"

[model]
mode = "live"
base_url = "https://provider.example/v1"
name = "my-model"
api_key = "provider-secret"
temperature = 0.4
context_messages = 12
max_reply_chars = 300

[qq]
api_url = "http://127.0.0.1:3001"
api_token = "bridge-secret"
login_url = "http://127.0.0.1:6099/webui/qq_login"
event_token = "event-secret-123456"

[agent]
friends = ["234567", "345678"]
prompt = """
自然聊天。
少用表情，不要连续追问。
"""
auto_start = true
reply_delay_seconds = 1.5
'''


def write_config(tmp_path, text="", name="qecho.toml"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def test_complete_toml_maps_to_runtime_settings_and_resolves_relative_data(tmp_path, monkeypatch):
    path = write_config(tmp_path, FULL_CONFIG)
    monkeypatch.chdir(tmp_path.parent)
    config = load_config(path)
    assert config.source_path == path.resolve()
    assert config.data_directory == tmp_path / "local state"
    assert config.server.host == "0.0.0.0" and config.server.port == 9876
    assert config.agent.friends == ["234567", "345678"] and config.agent.auto_start
    assert "少用表情" in config.agent.prompt
    patch = config.settings_patch()
    assert patch["model_api_key"] == "provider-secret"
    assert patch["bridge_token"] == "bridge-secret"
    assert patch["global_prompt"] == config.agent.prompt
    assert patch["simple_mode"] is True and "qq_self_id" not in patch
    assert "admin_token" not in patch and "event_token" not in patch
    assert not config.data_directory.exists()


def test_empty_preparation_config_and_utf8_bom_are_supported(tmp_path):
    path = write_config(
        tmp_path, '\ufeff[agent]\nfriends=[]\nauto_start=false\nprompt="先准备配置"\n'
    )
    config = load_config(path)
    assert config.agent.friends == [] and not config.agent.auto_start
    assert config.model.mode == "live" and config.model.base_url == ""
    assert config.server.admin_token == "" and config.qq.event_token == ""
    assert config.data_directory == tmp_path / ".data"
    assert config.settings_patch()["model_api_key"] == ""
    assert config.settings_patch()["bridge_token"] == ""


def test_absolute_data_directory_is_not_relative_to_current_working_directory(tmp_path):
    data = tmp_path / "absolute" / "data"
    path = write_config(tmp_path, f'[server]\ndata_dir="{data.as_posix()}"\n')
    assert load_config(path).data_directory == data
    assert not data.exists()


@pytest.mark.parametrize(
    "text, field",
    [
        ('[server]\nport="private-port-value"', "server.port"),
        ("[server]\nport=70000", "server.port"),
        ('[server]\nadmin_token="short-secret"', "server.admin_token"),
        ('[server]\nhost="http://bad-host"', "server.host"),
        ('[model]\napi_key=["raw-secret-value"]', "model.api_key"),
        ('[model]\nbase_url="file:///secret-path"', "model.base_url"),
        ('[model]\nbase_url="https://user:raw-secret-value@provider.example/v1"', "model.base_url"),
        ('[model]\nbase_url="https://provider.example/v1?key=raw-secret-value"', "model.base_url"),
        ("[model]\ncontext_messages=1", "model.context_messages"),
        ('[qq]\napi_url="javascript:raw-secret-value"', "qq.api_url"),
        ('[qq]\nlogin_url="file:///secret-path"', "qq.login_url"),
        ('[qq]\nevent_token="short-secret"', "qq.event_token"),
        ("[agent]\nfriends=[234567]", "agent.friends"),
        ('[agent]\nfriends=["not-qq"]', "agent.friends"),
        ('[agent]\nfriends=["234567","234567"]', "agent.friends"),
        ('[agent]\nauto_start="raw-secret-value"', "agent.auto_start"),
        ("[agent]\nreply_delay_seconds=-1", "agent.reply_delay_seconds"),
        ('unknown="raw-secret-value"', "unknown_field"),
        ('[model]\napi_secret="raw-secret-value"', "model.unknown_field"),
        ('source_path="raw-secret-value"', "unknown_field"),
    ],
)
def test_invalid_fields_reject_without_echoing_secret_values(tmp_path, text, field):
    with pytest.raises(ConfigError) as error:
        load_config(write_config(tmp_path, text))
    assert field in str(error.value)
    for secret in ("raw-secret-value", "private-port-value", "short-secret", "secret-path"):
        assert secret not in str(error.value)


@pytest.mark.parametrize(
    "text, field",
    [
        ("[agent]\nauto_start=true", "agent.auto_start"),
        (
            '[model]\nmode="demo"\nbase_url="https://provider.example/v1"\nname="chat"\n[agent]\nauto_start=true\nfriends=["234567"]',
            "agent.auto_start",
        ),
        (
            '[model]\nbase_url="https://provider.example/v1"\nname="chat"\n[agent]\nauto_start=true',
            "agent.friends",
        ),
        (
            '[model]\nbase_url="https://provider.example/v1"\nname="chat"\n[agent]\nauto_start=true\nfriends=["234567"]\nprompt="  "',
            "agent.prompt",
        ),
    ],
)
def test_auto_start_requires_live_model_scope_and_prompt(tmp_path, text, field):
    with pytest.raises(ConfigError, match=field):
        load_config(write_config(tmp_path, text))


def test_grouped_and_personal_prompts_preserve_default_scope_and_order(tmp_path):
    path = write_config(
        tmp_path,
        FULL_CONFIG
        + """
[[agent.prompt_groups]]
name = "  亲近好友  "
friends = ["444555", "555666"]
prompt = "语气轻松。"

[[agent.prompt_groups]]
name = "待分配"
prompt = "保留模板。"

[agent.friend_prompts]
"234567" = "少用表情。"
"555666" = "称呼对方为小林。"
""",
    )
    agent = load_config(path).agent
    assert agent.friends == ["234567", "345678"]
    assert agent.configured_friends == ["234567", "345678", "444555", "555666"]
    assert agent.group_for("234567") is None
    group = agent.group_for("555666")
    assert group.name == "亲近好友" and group.prompt == "语气轻松。"
    assert group.friends == ["444555", "555666"]
    assert agent.prompt_groups[1].friends == []
    assert agent.friend_prompts == {"234567": "少用表情。", "555666": "称呼对方为小林。"}


def test_auto_start_can_use_only_custom_groups(tmp_path):
    text = (
        FULL_CONFIG.replace('friends = ["234567", "345678"]', "friends = []")
        + """
[[agent.prompt_groups]]
name = "朋友"
friends = ["444555"]
prompt = "语气自然。"
"""
    )
    agent = load_config(write_config(tmp_path, text)).agent
    assert agent.auto_start and agent.configured_friends == ["444555"]


def test_empty_group_template_does_not_satisfy_auto_start_scope(tmp_path):
    text = (
        FULL_CONFIG.replace('friends = ["234567", "345678"]', "friends = []")
        + """
[[agent.prompt_groups]]
name = "模板"
prompt = "语气自然。"
"""
    )
    with pytest.raises(ConfigError, match="required_for_auto_start"):
        load_config(write_config(tmp_path, text))


@pytest.mark.parametrize(
    "text, expected",
    [
        (
            '[[agent.prompt_groups]]\nname="  "\nprompt="自然聊天"',
            "agent.prompt_groups.0.name",
        ),
        (
            '[[agent.prompt_groups]]\nname="朋友"\nprompt="  "',
            "agent.prompt_groups.0.prompt",
        ),
        (
            '[[agent.prompt_groups]]\nname="朋友"',
            "agent.prompt_groups.0.prompt",
        ),
        (
            '[[agent.prompt_groups]]\nname="朋友"\nprompt="自然聊天"\nfriends=[123456]',
            "agent.prompt_groups.0.friends",
        ),
        (
            '[[agent.prompt_groups]]\nname="朋友"\nprompt="自然聊天"\nfriends=["123456", "123456"]',
            "agent.prompt_groups.0.friends",
        ),
        (
            '[[agent.prompt_groups]]\nname="朋友"\nprompt="自然聊天"\n'
            '[[agent.prompt_groups]]\nname=" 朋友 "\nprompt="语气温和"',
            "duplicate_names",
        ),
        (
            '[agent]\nfriends=["123456"]\n'
            '[[agent.prompt_groups]]\nname="朋友"\nprompt="自然聊天"\nfriends=["123456"]',
            "duplicate_friends_across_groups",
        ),
        (
            '[[agent.prompt_groups]]\nname="朋友"\nprompt="自然聊天"\nfriends=["123456"]\n'
            '[[agent.prompt_groups]]\nname="同事"\nprompt="语气温和"\nfriends=["123456"]',
            "duplicate_friends_across_groups",
        ),
    ],
)
def test_invalid_prompt_groups_are_rejected_without_echoing_values(tmp_path, text, expected):
    with pytest.raises(ConfigError) as error:
        load_config(write_config(tmp_path, text))
    assert expected in str(error.value)
    assert "123456" not in str(error.value)


def test_authorized_scope_limit_applies_across_default_and_custom_groups(tmp_path):
    ids = [str(100000 + index) for index in range(501)]
    text = f"""[agent]
friends = {json.dumps(ids[:250])}
[[agent.prompt_groups]]
name = "朋友"
friends = {json.dumps(ids[250:])}
prompt = "语气自然。"
"""
    with pytest.raises(ConfigError, match="too_many_configured_friends"):
        load_config(write_config(tmp_path, text))


@pytest.mark.parametrize(
    "entries",
    [
        '" 123456"="新规则"\n"123456"="另一条规则"',
        '"123456 "="新规则"',
        '"not-a-qq-secret"="新规则"',
        '"123456"="  "',
        '"123456"=["private-prompt-value"]',
        '"999999"="个人提示词不能增加授权"',
    ],
)
def test_invalid_personal_prompt_keys_and_values_are_redacted(tmp_path, entries):
    text = f'[agent]\nfriends=["123456"]\n[agent.friend_prompts]\n{entries}'
    with pytest.raises(ConfigError, match=r"agent\.friend_prompts") as error:
        load_config(write_config(tmp_path, text))
    for submitted in ("123456", "999999", "not-a-qq-secret", "private-prompt-value"):
        assert submitted not in str(error.value)


@pytest.mark.parametrize(
    "text", ['[model]\napi_key="raw-secret-value', '[model\napi_key="raw-secret-value"']
)
def test_syntax_error_does_not_echo_the_bad_line(tmp_path, text):
    with pytest.raises(ConfigError, match="toml_syntax") as error:
        load_config(write_config(tmp_path, text))
    assert "raw-secret-value" not in str(error.value)


def test_invalid_encoding_and_missing_files_are_safe(tmp_path):
    path = tmp_path / "config.toml"
    path.write_bytes(b"\xff\xfe\xff")
    with pytest.raises(ConfigError, match="encoding_error"):
        load_config(path)
    with pytest.raises(ConfigError, match="file_not_found"):
        load_config(tmp_path / "raw-secret-value.toml")


def test_config_representation_does_not_include_access_credentials(tmp_path):
    config = load_config(write_config(tmp_path, FULL_CONFIG))
    for secret in (
        "admin-secret-123456",
        "provider-secret",
        "bridge-secret",
        "event-secret-123456",
    ):
        assert secret not in repr(config)


@pytest.mark.parametrize("prefix", ["", "[model]\n"])
def test_unknown_field_names_are_also_redacted_if_a_credential_was_pasted_as_a_key(
    tmp_path, prefix
):
    secret = "pasted-secret-key-123456"
    with pytest.raises(ConfigError, match="unknown_field") as error:
        load_config(write_config(tmp_path, prefix + f'"{secret}"="value"'))
    assert secret not in str(error.value)


def test_file_import_clears_old_credentials_authorizations_and_pending_work(tmp_path):
    store = Store(tmp_path / "state.db")
    store.update_settings(
        {
            "enabled": True,
            "qq_self_id": "123456",
            "owner_name": "旧设置",
            "model_api_key": "old-model",
            "bridge_token": "old-bridge",
        }
    )
    contact = store.create_contact(
        {"qq_id": "234567", "name": "保留联系人", "enabled": True, "persona": "保留人格"}
    )
    records = {
        status: store.add_message(
            contact["id"],
            "incoming" if status in {"received", "processing", "processed"} else "outgoing",
            "原消息内容",
            "qq",
            status,
        )
        for status in [
            "received",
            "processing",
            "processed",
            "generating",
            "draft",
            "sending",
            "sent",
            "unknown",
        ]
    }
    revision = store.get_settings()["revision"]
    config = load_config(write_config(tmp_path))
    result = store.import_file_settings(config.settings_patch())
    assert result["qq_self_id"] == "123456" and result["revision"] == revision + 1
    assert result["simple_mode"] and not result["enabled"]
    assert result["owner_name"] == ""
    assert not result["model_api_key_set"] and not result["bridge_token_set"]
    assert store.get_secret("model_api_key") == store.get_secret("bridge_token") == ""
    saved_contact = store.get_contact(contact["id"])
    assert not saved_contact["enabled"] and saved_contact["persona"] == "保留人格"
    for status in ["received", "processing", "generating", "draft"]:
        assert store.get_message(records[status]["id"])["status"] == "cancelled"
    for status in ["processed", "sending", "sent", "unknown"]:
        saved = store.get_message(records[status]["id"])
        assert saved["status"] == status and saved["content"] == "原消息内容"
    store.close()


def test_file_import_replaces_keys_but_never_rebinds_existing_qq(tmp_path):
    store = Store(tmp_path / "state.db")
    store.update_settings({"qq_self_id": "123456", "model_api_key": "old-model"})
    config = load_config(write_config(tmp_path, FULL_CONFIG))
    patch = config.settings_patch() | {"qq_self_id": "999999", "enabled": True}
    result = store.import_file_settings(patch)
    assert result["qq_self_id"] == "123456" and not result["enabled"]
    assert store.get_secret("model_api_key") == "provider-secret"
    assert store.get_secret("bridge_token") == "bridge-secret"
    assert "provider-secret" not in str(result) and "bridge-secret" not in str(result)
    store.close()


def test_file_import_rolls_back_settings_keys_contacts_and_messages_together(tmp_path):
    store = Store(tmp_path / "state.db")
    store.update_settings(
        {"enabled": True, "model_api_key": "old-model", "bridge_token": "old-bridge"}
    )
    contact = store.create_contact({"qq_id": "234567", "name": "原联系人", "enabled": True})
    draft = store.add_message(contact["id"], "outgoing", "待发草稿", "qq", "draft")
    before = store.get_settings()
    store.db.execute(
        "CREATE TRIGGER reject_contact_update BEFORE UPDATE ON contacts BEGIN SELECT RAISE(ABORT,'forced_failure'); END"
    )
    config = load_config(write_config(tmp_path, FULL_CONFIG))
    with pytest.raises(sqlite3.IntegrityError):
        store.import_file_settings(config.settings_patch())
    assert store.get_settings() == before
    assert store.get_secret("model_api_key") == "old-model"
    assert store.get_secret("bridge_token") == "old-bridge"
    assert store.get_contact(contact["id"])["enabled"]
    assert store.get_message(draft["id"])["status"] == "draft"
    store.close()


@pytest.mark.parametrize(
    "section,field",
    [("server", "admin_token"), ("qq", "event_token"), ("model", "api_key"), ("qq", "api_token")],
)
@pytest.mark.parametrize(
    "value",
    [
        "01234567\r\n89abcdef",
        "\n0123456789abcdef",
        "0123456789abcdef\r",
        "01234567\t89abcdef",
        "01234567\x0089abcdef",
        "01234567\x1b89abcdef",
        "01234567\x7f89abcdef",
        "01234567密钥89abcdef",
    ],
)
def test_header_credentials_reject_controls_and_non_ascii_before_whitespace_trimming(
    tmp_path, section, field, value
):
    text = f"[{section}]\n{field}={json.dumps(value)}\n"
    with pytest.raises(ConfigError) as error:
        load_config(write_config(tmp_path, text))
    assert f"{section}.{field}" in str(error.value)
    assert "01234567" not in str(error.value)


@pytest.mark.parametrize(
    "section,field",
    [("server", "admin_token"), ("qq", "event_token"), ("model", "api_key"), ("qq", "api_token")],
)
def test_printable_ascii_credentials_are_accepted_and_outer_spaces_are_trimmed(
    tmp_path, section, field
):
    value = "  api-secret.~-_+/=12345  "
    config = load_config(write_config(tmp_path, f"[{section}]\n{field}={json.dumps(value)}\n"))
    assert getattr(getattr(config, section), field) == value.strip(" ")
