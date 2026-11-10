import sys
from types import SimpleNamespace

import pytest

from qecho import __main__ as cli


@pytest.fixture
def launcher(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for name in ("QECHO_CONFIG", "QECHO_HOST", "QECHO_PORT"):
        monkeypatch.delenv(name, raising=False)
    calls = []
    monkeypatch.setattr(cli.uvicorn, "run", lambda *args, **kwargs: calls.append((args, kwargs)))
    constructed = []

    def create_app(**kwargs):
        constructed.append(kwargs)
        return "constructed-file-app"

    monkeypatch.setitem(sys.modules, "qecho.app", SimpleNamespace(create_app=create_app))
    return tmp_path, calls, constructed


def test_discovered_file_owns_server_values_and_blank_tokens_override_environment(
    launcher, monkeypatch
):
    path, calls, constructed = launcher
    (path / "qecho.toml").write_text('[server]\nhost="0.0.0.0"\nport=9000\n', encoding="utf-8")
    monkeypatch.setenv("QECHO_HOST", "environment-host")
    monkeypatch.setenv("QECHO_PORT", "7000")
    monkeypatch.setenv("QECHO_ADMIN_TOKEN", "environment-admin-token")
    monkeypatch.setenv("QECHO_WEBHOOK_TOKEN", "environment-webhook-token")
    cli.main([])
    assert len(constructed) == 1
    config = constructed[0]["file_config"]
    assert config.server.admin_token == "" and config.qq.event_token == ""
    assert calls == [
        (
            ("constructed-file-app",),
            {"host": "0.0.0.0", "port": 9000, "workers": 1, "access_log": False},
        )
    ]


def test_explicit_file_wins_over_environment_and_cwd_discovery(launcher, monkeypatch):
    path, calls, constructed = launcher
    explicit = path / "custom config.toml"
    explicit.write_text("[server]\nport=9001\n", encoding="utf-8")
    (path / "qecho.toml").write_text("[server]\nport=9002\n", encoding="utf-8")
    monkeypatch.setenv("QECHO_CONFIG", "does-not-exist.toml")
    cli.main(["--config", str(explicit)])
    assert constructed[0]["file_config"].source_path == explicit
    assert calls[0][1]["port"] == 9001


def test_environment_configuration_path_is_supported(launcher, monkeypatch):
    path, calls, constructed = launcher
    config = path / "environment.toml"
    config.write_text("", encoding="utf-8")
    monkeypatch.setenv("QECHO_CONFIG", str(config))
    cli.main([])
    assert constructed[0]["file_config"].source_path == config
    assert len(calls) == 1


def test_check_config_performs_no_app_or_server_start(launcher, capsys):
    path, calls, constructed = launcher
    (path / "qecho.toml").write_text('[model]\napi_key="do-not-print-secret"\n', encoding="utf-8")
    cli.main(["--check-config"])
    output = capsys.readouterr()
    assert "检查通过" in output.out
    assert "do-not-print-secret" not in output.out + output.err
    assert calls == constructed == []
    assert not (path / ".data").exists()


@pytest.mark.parametrize("arguments", [["--config", "missing.toml"], ["--config", ""]])
def test_explicit_missing_or_empty_file_never_falls_back_to_existing_discovered_file(
    launcher, arguments
):
    path, calls, constructed = launcher
    (path / "qecho.toml").write_text("", encoding="utf-8")
    with pytest.raises(SystemExit) as error:
        cli.main(arguments)
    assert error.value.code == 2 and calls == constructed == []


def test_environment_missing_config_does_not_fall_back_to_web_mode(launcher, monkeypatch):
    _, calls, constructed = launcher
    monkeypatch.setenv("QECHO_CONFIG", "missing.toml")
    with pytest.raises(SystemExit) as error:
        cli.main([])
    assert error.value.code == 2 and calls == constructed == []


def test_invalid_configuration_error_does_not_echo_input(launcher, capsys):
    path, calls, constructed = launcher
    (path / "qecho.toml").write_text('[model]\napi_key=["do-not-print-secret"]\n', encoding="utf-8")
    with pytest.raises(SystemExit) as error:
        cli.main([])
    output = capsys.readouterr()
    assert error.value.code == 2
    assert "model.api_key" in output.err and "do-not-print-secret" not in output.err
    assert calls == constructed == []


def test_check_config_without_file_fails_instead_of_starting_web_mode(launcher):
    _, calls, constructed = launcher
    with pytest.raises(SystemExit) as error:
        cli.main(["--check-config"])
    assert error.value.code == 2 and calls == constructed == []


def test_no_file_preserves_legacy_environment_and_web_mode(launcher, monkeypatch):
    _, calls, constructed = launcher
    monkeypatch.setenv("QECHO_HOST", "127.0.0.2")
    monkeypatch.setenv("QECHO_PORT", "7777")
    cli.main([])
    assert calls == [
        (("qecho.app:app",), {"host": "127.0.0.2", "port": 7777, "workers": 1, "access_log": False})
    ]
    assert constructed == []


def test_bad_legacy_port_is_safe_and_does_not_start_server(launcher, monkeypatch, capsys):
    _, calls, constructed = launcher
    monkeypatch.setenv("QECHO_PORT", "do-not-print-secret")
    with pytest.raises(SystemExit) as error:
        cli.main([])
    output = capsys.readouterr()
    assert error.value.code == 2 and "QECHO_PORT" in output.err
    assert "do-not-print-secret" not in output.err
    assert calls == constructed == []
