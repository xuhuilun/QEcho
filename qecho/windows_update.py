"""Discover and verify installers from QEcho's official Windows release channel."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from time import monotonic
from urllib.parse import urljoin, urlsplit

import httpx

_REPOSITORY = "xuhuilun/QEcho"
_LATEST_RELEASE = f"https://api.github.com/repos/{_REPOSITORY}/releases/latest"
_MANIFEST_NAME = "windows-x64.json"
_ASSET_HOSTS = {
    "release-assets.githubusercontent.com",
    "objects.githubusercontent.com",
    "github-releases.githubusercontent.com",
}
_REDIRECT_CODES = {301, 302, 303, 307, 308}
_MAX_REDIRECTS = 4
_MAX_RELEASE_BYTES = 1024 * 1024
_MAX_MANIFEST_BYTES = 16 * 1024
_MAX_INSTALLER_BYTES = 256 * 1024 * 1024
_TEMP_PREFIX = "qecho-update-"
_VERSION_PART = r"(?:0|[1-9][0-9]{0,8})"
_VERSION = re.compile(rf"{_VERSION_PART}\.{_VERSION_PART}\.{_VERSION_PART}")
_INSTALLER_NAME = re.compile(rf"QEcho-Setup-({_VERSION.pattern})-x64\.exe")
_SHA256 = re.compile(r"[0-9a-fA-F]{64}")
_SIGNATURE_CHECK = r"""
$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSHOME 'Modules\Microsoft.PowerShell.Security\Microsoft.PowerShell.Security.psd1')
$current = Microsoft.PowerShell.Security\Get-AuthenticodeSignature -LiteralPath $env:QECHO_VERIFY_CURRENT
$installer = Microsoft.PowerShell.Security\Get-AuthenticodeSignature -LiteralPath $env:QECHO_VERIFY_INSTALLER
if ($current.Status -ne 'Valid' -or -not $current.SignerCertificate) { exit 10 }
if ($installer.Status -ne 'Valid' -or -not $installer.SignerCertificate) { exit 11 }
if ([Convert]::ToBase64String($current.SignerCertificate.RawData) -cne
    [Convert]::ToBase64String($installer.SignerCertificate.RawData)) { exit 12 }
exit 0
"""


class UpdateError(RuntimeError):
    """Public update failure without URLs, response bodies or local file paths."""


@dataclass(frozen=True)
class WindowsUpdate:
    version: str
    url: str
    size: int
    sha256: str


def _version(value: str) -> tuple[int, ...] | None:
    if not isinstance(value, str) or not _VERSION.fullmatch(value):
        return None
    return tuple(int(part) for part in value.split("."))


def _installer_name(version: str) -> str:
    return f"QEcho-Setup-{version}-x64.exe"


def _asset_url(version: str, filename: str) -> str:
    return f"https://github.com/{_REPOSITORY}/releases/download/v{version}/{filename}"


def _asset(assets: list, name: str, version: str) -> dict | None:
    matches = [asset for asset in assets if isinstance(asset, dict) and asset.get("name") == name]
    if not matches:
        return None
    if len(matches) != 1 or matches[0].get("browser_download_url") != _asset_url(version, name):
        raise UpdateError("发布资产地址无效，已停止更新。")
    return matches[0]


def _http_client() -> httpx.Client:
    return httpx.Client(
        follow_redirects=False,
        trust_env=False,
        timeout=httpx.Timeout(15.0, connect=5.0),
        headers={"User-Agent": "QEcho-Windows", "Accept-Encoding": "identity"},
    )


def _redirect(url: str, location: str) -> str:
    if (
        not location
        or len(location) > 8192
        or any(
            ord(character) < 33 or ord(character) > 126 or character == "\\"
            for character in location
        )
    ):
        raise UpdateError("发布资产的下载跳转无效。")
    try:
        target = urljoin(url, location)
        parsed = urlsplit(target)
        allowed = (
            parsed.scheme == "https"
            and parsed.hostname in _ASSET_HOSTS
            and parsed.port in (None, 443)
            and parsed.username is None
            and parsed.password is None
            and not parsed.fragment
        )
    except ValueError:
        allowed = False
    if not allowed:
        raise UpdateError("发布资产的下载跳转离开了 GitHub 官方资产主机。")
    return target


@contextmanager
def _open_asset(client: httpx.Client, url: str):
    for _ in range(_MAX_REDIRECTS + 1):
        with client.stream("GET", url, follow_redirects=False) as response:
            if response.status_code == 200:
                yield response
                return
            if response.status_code not in _REDIRECT_CODES:
                raise UpdateError("GitHub 发布资产暂时无法下载。")
            url = _redirect(url, response.headers.get("location", ""))
    raise UpdateError("发布资产的下载跳转次数过多。")


def _chunks(response: httpx.Response, limit: int, seconds: float):
    if response.headers.get("content-encoding", "identity").lower() != "identity":
        raise UpdateError("GitHub 返回了不受支持的下载编码。")
    header = response.headers.get("content-length")
    declared = None
    if header is not None:
        if not re.fullmatch(r"[0-9]{1,20}", header) or int(header) > limit:
            raise UpdateError("下载文件的大小无效或超过限制。")
        declared = int(header)
    deadline = monotonic() + seconds
    size = 0
    for chunk in response.iter_bytes():
        size += len(chunk)
        if monotonic() > deadline:
            raise UpdateError("下载超时，请稍后重试。")
        if size > limit or (declared is not None and size > declared):
            raise UpdateError("下载文件超过声明大小或下载限制。")
        yield chunk
    if declared is not None and size != declared:
        raise UpdateError("下载文件大小与声明不一致。")


def _read_json(response: httpx.Response, limit: int) -> dict:
    try:
        payload = json.loads(b"".join(_chunks(response, limit, 30)))
    except (ValueError, RecursionError):
        raise UpdateError("GitHub 发布数据格式无效。") from None
    if not isinstance(payload, dict):
        raise UpdateError("GitHub 发布数据格式无效。")
    return payload


def check_for_update(current_version: str) -> WindowsUpdate | None:
    """Read the latest published stable release and its Windows manifest."""
    current = _version(current_version)
    if current is None:
        raise UpdateError("当前客户端版本号无效。")
    try:
        with _http_client() as client:
            with client.stream(
                "GET", _LATEST_RELEASE, headers={"Accept": "application/vnd.github+json"}
            ) as response:
                if response.status_code == 404:
                    return None
                if response.status_code != 200:
                    raise UpdateError("暂时无法查询 GitHub 正式发布。")
                release = _read_json(response, _MAX_RELEASE_BYTES)
            if release.get("draft") is not False or release.get("prerelease") is not False:
                return None
            tag = release.get("tag_name")
            if not isinstance(tag, str) or not tag.startswith("v"):
                return None
            version = tag[1:]
            latest = _version(version)
            if latest is None or latest <= current:
                return None

            assets = release.get("assets")
            if not isinstance(assets, list):
                raise UpdateError("GitHub 发布资产列表格式无效。")
            installer = _asset(assets, _installer_name(version), version)
            manifest = _asset(assets, _MANIFEST_NAME, version)
            if installer is None or manifest is None:
                return None
            with _open_asset(client, manifest["browser_download_url"]) as response:
                metadata = _read_json(response, _MAX_MANIFEST_BYTES)
            size = metadata.get("size")
            sha256 = metadata.get("sha256")
            if (
                metadata.get("version") != version
                or type(size) is not int
                or not 0 < size <= _MAX_INSTALLER_BYTES
                or ("size" in installer and installer["size"] != size)
                or not isinstance(sha256, str)
                or not _SHA256.fullmatch(sha256)
            ):
                raise UpdateError("Windows 更新清单的版本、大小或哈希无效。")
            return WindowsUpdate(version, installer["browser_download_url"], size, sha256.lower())
    except (httpx.HTTPError, httpx.InvalidURL):
        raise UpdateError("暂时无法连接 GitHub 发布服务。") from None


def _powershell() -> Path:
    from ctypes import WinDLL, create_unicode_buffer

    system_directory = create_unicode_buffer(32768)
    count = WinDLL("kernel32").GetSystemDirectoryW(system_directory, len(system_directory))
    if not 0 < count < len(system_directory):
        raise UpdateError("无法找到用于签名校验的 Windows PowerShell。")
    return Path(system_directory.value) / "WindowsPowerShell" / "v1.0" / "powershell.exe"


def verify_installer_signature(installer: Path, current_executable: Path) -> None:
    """Require trusted Authenticode signatures with identical signer certificates."""
    if sys.platform != "win32":
        raise UpdateError("只能在 Windows 验证安装包签名。")
    try:
        powershell = _powershell()
        # Keep paths out of the command text. PowerShell reads them as literal
        # paths, including brackets, Unicode, spaces and apostrophes.
        environment = os.environ | {
            "QECHO_VERIFY_CURRENT": str(Path(current_executable).resolve(strict=True)),
            "QECHO_VERIFY_INSTALLER": str(Path(installer).resolve(strict=True)),
        }
        result = subprocess.run(
            [
                str(powershell),
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                _SIGNATURE_CHECK,
            ],
            env=environment,
            cwd=powershell.parent,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            timeout=60,
            check=False,
        )
    except (OSError, RuntimeError, subprocess.TimeoutExpired):
        raise UpdateError("无法完成 Windows 安装包签名校验。") from None
    failures = {
        10: "当前客户端没有 Windows 信任的签名，无法自动更新。",
        11: "安装包没有 Windows 信任的签名，已停止更新。",
        12: "安装包的签名证书与当前客户端不同，已停止更新。",
    }
    if result.returncode != 0:
        raise UpdateError(failures.get(result.returncode, "无法完成 Windows 安装包签名校验。"))


def discard_update(path: Path) -> None:
    """Clear a download produced by this module without traversing a directory."""
    path = Path(path).absolute()
    directory = path.parent
    try:
        if (
            _INSTALLER_NAME.fullmatch(path.name)
            and directory.name.startswith(_TEMP_PREFIX)
            and directory.resolve() == Path(tempfile.gettempdir()).resolve() / directory.name
        ):
            path.unlink(missing_ok=True)
            directory.rmdir()
    except (OSError, RuntimeError):
        # The installer may still be holding an open handle on Windows.
        pass


def download_update(update: WindowsUpdate, current_executable: Path) -> Path:
    """Return a verified installer in a fresh temporary directory."""
    if (
        _version(update.version) is None
        or update.url != _asset_url(update.version, _installer_name(update.version))
        or type(update.size) is not int
        or not 0 < update.size <= _MAX_INSTALLER_BYTES
        or not isinstance(update.sha256, str)
        or not _SHA256.fullmatch(update.sha256)
    ):
        raise UpdateError("Windows 更新信息无效。")
    destination = None
    verified = False
    try:
        directory = Path(tempfile.mkdtemp(prefix=_TEMP_PREFIX))
        destination = directory / _installer_name(update.version)
        digest = hashlib.sha256()
        size = 0
        with _http_client() as client, _open_asset(client, update.url) as response:
            with destination.open("xb") as file:
                for chunk in _chunks(response, update.size, 300):
                    file.write(chunk)
                    digest.update(chunk)
                    size += len(chunk)
        if size != update.size or digest.hexdigest() != update.sha256.lower():
            raise UpdateError("安装包的大小或 SHA-256 校验失败。")
        verify_installer_signature(destination, current_executable)
        verified = True
        return destination
    except (OSError, httpx.HTTPError, httpx.InvalidURL):
        raise UpdateError("无法下载或写入更新安装包。") from None
    finally:
        if destination is not None and not verified:
            discard_update(destination)
