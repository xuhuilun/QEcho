import hashlib
import subprocess
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from qecho import windows_update as updater

_LATEST = "https://api.github.com/repos/xuhuilun/QEcho/releases/latest"
_ASSETS = "https://github.com/xuhuilun/QEcho/releases/download/v1.10.0/"
_SETUP = "QEcho-Setup-1.10.0-x64.exe"
_MANIFEST = "windows-x64.json"
_INSTALLER_BYTES = b"test fixture, not an executable"
_SHA = hashlib.sha256(_INSTALLER_BYTES).hexdigest()


def release():
    return {
        "tag_name": "v1.10.0",
        "draft": False,
        "prerelease": False,
        "assets": [
            {
                "name": _SETUP,
                "browser_download_url": _ASSETS + _SETUP,
                "size": len(_INSTALLER_BYTES),
            },
            {"name": _MANIFEST, "browser_download_url": _ASSETS + _MANIFEST},
        ],
    }


def update():
    return updater.WindowsUpdate("1.10.0", _ASSETS + _SETUP, len(_INSTALLER_BYTES), _SHA)


@pytest.fixture
def http(monkeypatch):
    def install(handler):
        requests = []

        def record(request):
            requests.append(str(request.url))
            return handler(request)

        monkeypatch.setattr(
            updater, "_http_client", lambda: httpx.Client(transport=httpx.MockTransport(record))
        )
        return requests

    return install


def test_numeric_stable_release_discovers_manifest_on_allowed_cdn(http):
    cdn = "https://release-assets.githubusercontent.com/path?download=1"
    requests = http(
        lambda request: {
            _LATEST: httpx.Response(200, json=release()),
            _ASSETS + _MANIFEST: httpx.Response(302, headers={"Location": cdn}),
            cdn: httpx.Response(
                200,
                json={"version": "1.10.0", "size": len(_INSTALLER_BYTES), "sha256": _SHA.upper()},
            ),
        }[str(request.url)]
    )
    assert updater.check_for_update("1.9.0") == update()
    assert requests == [_LATEST, _ASSETS + _MANIFEST, cdn]


@pytest.mark.parametrize(
    "changes,current",
    [
        ({"draft": True}, "1.0.0"),
        ({"prerelease": True}, "1.0.0"),
        ({"tag_name": "v1.10.0-rc1"}, "1.0.0"),
        ({"tag_name": "1.10.0"}, "1.0.0"),
        ({"tag_name": "v01.10.0"}, "1.0.0"),
        ({"tag_name": "v1.10.0+build"}, "1.0.0"),
        ({}, "1.10.0"),
        ({}, "2.0.0"),
        ({"assets": []}, "1.0.0"),
    ],
)
def test_no_update_download_when_release_is_not_eligible(http, changes, current):
    requests = http(lambda _: httpx.Response(200, json=release() | changes))
    assert updater.check_for_update(current) is None
    assert requests == [_LATEST]


def test_no_published_release_is_not_an_error(http):
    http(lambda _: httpx.Response(404))
    assert updater.check_for_update("1.0.0") is None


@pytest.mark.parametrize(
    "target",
    [
        "https://github.com/another/QEcho/releases/download/v1.10.0/windows-x64.json",
        "https://github.com/xuhuilun/QEcho/releases/download/v1.10.0/windows-x64.json?x=secret",
        "https://github.com/xuhuilun/QEcho/releases/download/v1.9.0/windows-x64.json",
        "https://example.invalid/windows-x64.json",
    ],
)
def test_manifest_first_hop_must_match_official_repository_tag_and_name(http, target):
    payload = release()
    payload["assets"][1]["browser_download_url"] = target
    requests = http(lambda _: httpx.Response(200, json=payload))
    with pytest.raises(updater.UpdateError, match="资产地址") as error:
        updater.check_for_update("1.0.0")
    assert "secret" not in str(error.value)
    assert requests == [_LATEST]


@pytest.mark.parametrize(
    "target",
    [
        "http://release-assets.githubusercontent.com/archive",
        "https://release-assets.githubusercontent.com.attacker.invalid/archive",
        "https://user@release-assets.githubusercontent.com/archive",
        "https://release-assets.githubusercontent.com:8443/archive",
        "https://127.0.0.1/archive",
    ],
)
def test_redirect_to_unapproved_endpoint_is_never_requested(http, target):
    requests = http(lambda _: httpx.Response(302, headers={"Location": target}))
    with pytest.raises(updater.UpdateError, match="跳转"):
        updater.download_update(update(), Path("installed.exe"))
    assert requests == [_ASSETS + _SETUP]


def test_cdn_redirects_are_bounded(http, monkeypatch, tmp_path):
    monkeypatch.setattr(updater.tempfile, "tempdir", str(tmp_path))
    requests = http(
        lambda _: httpx.Response(
            302, headers={"Location": "https://release-assets.githubusercontent.com/archive"}
        )
    )
    with pytest.raises(updater.UpdateError, match="次数"):
        updater.download_update(update(), Path("installed.exe"))
    assert len(requests) == 5
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "changes",
    [
        {"version": "v1.10.0"},
        {"version": "1.9.0"},
        {"size": True},
        {"size": 0},
        {"size": 257 * 1024 * 1024},
        {"size": len(_INSTALLER_BYTES) + 1},
        {"sha256": "not-a-hash"},
    ],
)
def test_manifest_must_match_release_version_and_size(http, changes):
    metadata = {"version": "1.10.0", "size": len(_INSTALLER_BYTES), "sha256": _SHA} | changes
    http(
        lambda request: httpx.Response(
            200, json=release() if str(request.url) == _LATEST else metadata
        )
    )
    with pytest.raises(updater.UpdateError, match="清单"):
        updater.check_for_update("1.0.0")


class Chunks(httpx.SyncByteStream):
    def __iter__(self):
        yield b"x" * 16384
        yield b"x"


def test_manifest_stream_without_content_length_is_bounded(http):
    http(
        lambda request: (
            httpx.Response(200, json=release())
            if str(request.url) == _LATEST
            else httpx.Response(200, stream=Chunks())
        )
    )
    with pytest.raises(updater.UpdateError, match="下载限制"):
        updater.check_for_update("1.0.0")


def test_valid_download_is_verified_and_can_be_discarded(http, monkeypatch, tmp_path):
    monkeypatch.setattr(updater.tempfile, "tempdir", str(tmp_path))
    verified = []
    monkeypatch.setattr(
        updater,
        "verify_installer_signature",
        lambda path, current: verified.append((path, current)),
    )
    http(lambda _: httpx.Response(200, content=_INSTALLER_BYTES))
    current = tmp_path / "installed.exe"
    path = updater.download_update(update(), current)
    assert path.read_bytes() == _INSTALLER_BYTES
    assert verified == [(path, current)]
    updater.discard_update(path)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "body", [_INSTALLER_BYTES[:-1], _INSTALLER_BYTES + b"x", b"x" * len(_INSTALLER_BYTES)]
)
def test_hash_or_size_mismatch_prevents_signature_check_and_cleans_up(
    http, monkeypatch, tmp_path, body
):
    monkeypatch.setattr(updater.tempfile, "tempdir", str(tmp_path))
    monkeypatch.setattr(
        updater, "verify_installer_signature", lambda *_: pytest.fail("verification called")
    )
    http(lambda _: httpx.Response(200, content=body))
    with pytest.raises(updater.UpdateError, match="大小"):
        updater.download_update(update(), tmp_path / "installed.exe")
    assert list(tmp_path.iterdir()) == []


def test_signature_failure_cleans_up_successfully_downloaded_bytes(http, monkeypatch, tmp_path):
    def reject(*_):
        raise updater.UpdateError("签名证书不匹配")

    monkeypatch.setattr(updater.tempfile, "tempdir", str(tmp_path))
    monkeypatch.setattr(updater, "verify_installer_signature", reject)
    http(lambda _: httpx.Response(200, content=_INSTALLER_BYTES))
    with pytest.raises(updater.UpdateError, match="签名"):
        updater.download_update(update(), tmp_path / "installed.exe")
    assert list(tmp_path.iterdir()) == []


def test_forged_download_object_does_not_trigger_http(http):
    requests = http(lambda _: pytest.fail("unapproved HTTP request"))
    with pytest.raises(updater.UpdateError, match="更新信息"):
        updater.download_update(
            replace(update(), url="https://example.invalid/setup.exe"), Path("installed.exe")
        )
    assert requests == []


@pytest.mark.parametrize(
    "returncode,expected",
    [
        (0, None),
        (10, "当前客户端"),
        (11, "安装包"),
        (12, "签名证书与当前客户端不同"),
        (1, "无法完成"),
    ],
)
def test_authenticode_statuses_fail_closed_and_paths_stay_literal(
    monkeypatch, tmp_path, returncode, expected
):
    current = tmp_path / "my [app] 的 QEcho's.exe"
    installer = tmp_path / _SETUP
    current.touch()
    installer.touch()
    powershell = tmp_path / "System32" / "powershell.exe"
    monkeypatch.setattr(updater, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(updater, "_powershell", lambda: powershell)

    def run(args, **options):
        assert args[0] == str(powershell)
        assert "-NoProfile" in args and "-NonInteractive" in args
        assert str(current) not in args[-1] and str(installer) not in args[-1]
        assert "-LiteralPath" in args[-1] and ".RawData" in args[-1]
        assert options["env"]["QECHO_VERIFY_CURRENT"] == str(current.resolve())
        assert options["env"]["QECHO_VERIFY_INSTALLER"] == str(installer.resolve())
        return SimpleNamespace(returncode=returncode)

    monkeypatch.setattr(updater.subprocess, "run", run)
    if expected:
        with pytest.raises(updater.UpdateError, match=expected):
            updater.verify_installer_signature(installer, current)
    else:
        updater.verify_installer_signature(installer, current)


def test_signature_command_failure_does_not_expose_local_paths(monkeypatch, tmp_path):
    installer = tmp_path / _SETUP
    installer.touch()
    monkeypatch.setattr(updater, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(updater, "_powershell", lambda: tmp_path / "powershell.exe")

    def timeout(*args, **options):
        raise subprocess.TimeoutExpired(cmd="secret-path", timeout=60)

    monkeypatch.setattr(updater.subprocess, "run", timeout)
    with pytest.raises(updater.UpdateError, match="无法完成") as error:
        updater.verify_installer_signature(installer, installer)
    assert "secret-path" not in str(error.value)


def test_signature_verification_cannot_be_skipped_on_other_platforms(monkeypatch):
    monkeypatch.setattr(updater, "sys", SimpleNamespace(platform="darwin"))
    with pytest.raises(updater.UpdateError, match="Windows"):
        updater.verify_installer_signature(Path(_SETUP), Path("QEcho.exe"))
