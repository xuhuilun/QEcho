"""Small HTTP boundaries for a chat-completions model and OneBot 11.

Sending a message is deliberately never retried here: a missing response does
not prove that QQ did not deliver the message.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

import httpx


class ProviderError(Exception):
    """A model request could not produce a usable reply."""


class BridgeError(Exception):
    """Separate delivery uncertainty from retryable read-only readiness checks."""

    def __init__(self, message: str, uncertain: bool = False, *, retryable: bool = False):
        super().__init__(message)
        self.uncertain = uncertain
        self.retryable = retryable


def _endpoint(base_url: str, action: str) -> str:
    """Keep credentials in headers and preserve an explicitly configured path."""
    if not isinstance(base_url, str):
        raise ValueError("请填写有效的 HTTP 或 HTTPS 服务地址")
    value = base_url.strip().rstrip("/")
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or any(character.isspace() for character in value)
    ):
        raise ValueError("服务地址须使用 HTTP 或 HTTPS，且不能包含凭证、查询参数或片段")
    # Accessing port also rejects malformed/non-numeric ports before a request.
    parsed.port
    return f"{value}/{action}"


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"} if token else {}


class _HTTPClient:
    def __init__(self, client: httpx.AsyncClient | None = None):
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(45, connect=10))

    async def close(self) -> None:
        """Injected clients remain owned by their caller."""
        if self._owns_client:
            await self._client.aclose()


class ModelClient(_HTTPClient):
    async def generate(
        self, settings: dict[str, Any], messages: list[dict[str, str]], api_key: str
    ) -> str:
        """Generate plain text; demo mode never performs a network request."""
        if settings.get("model_mode", "demo") == "demo":
            latest = next(
                (
                    message["content"]
                    for message in reversed(messages)
                    if message.get("role") == "user"
                ),
                "你好",
            )
            return f"[演练模型] 收到“{latest[:80]}”。配置真实模型后，会根据你设置的人格和语气生成回复。"

        try:
            url = _endpoint(settings.get("model_base_url", ""), "chat/completions")
            model = str(settings.get("model_name", "")).strip()
            if not model:
                raise ValueError("请填写模型名称")
            response = await self._client.post(
                url,
                headers=_headers(api_key),
                json={
                    "model": model,
                    "messages": messages,
                    "temperature": settings.get("temperature", 0.7),
                    "stream": False,
                },
                follow_redirects=False,
            )
        except (ValueError, httpx.InvalidURL) as exc:
            raise ProviderError("模型地址或配置无效，请检查连接设置") from exc
        except httpx.RequestError as exc:
            raise ProviderError("模型请求失败或超时，请检查服务地址与网络") from exc

        if response.status_code < 200 or response.status_code >= 300:
            # Provider response bodies may echo API keys or private request data.
            raise ProviderError(f"模型服务返回 HTTP {response.status_code}，请检查服务配置")
        try:
            payload = response.json()
            content = payload["choices"][0]["message"]["content"]
            if not isinstance(content, str) or not content.strip():
                raise ValueError("empty model reply")
            limit = int(settings.get("max_reply_chars", 800))
            if limit < 1:
                raise ValueError("invalid reply length")
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ProviderError("模型返回了空回复或不兼容的响应格式") from exc
        content = content.strip()
        if len(content) > limit:
            raise ProviderError("模型回复超过长度限制，请调整提示词或回复长度后重新生成")
        return content


class BridgeClient(_HTTPClient):
    async def _call(
        self, base_url: str, token: str, action: str, payload: dict[str, Any], *, sending: bool
    ) -> Any:
        try:
            url = _endpoint(base_url, action)
            response = await self._client.post(
                url,
                headers=_headers(token),
                json=payload,
                follow_redirects=False,
            )
        except (ValueError, httpx.InvalidURL) as exc:
            raise BridgeError("QQ 桥接地址无效，请检查连接设置") from exc
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as exc:
            raise BridgeError(
                "无法连接 QQ 桥接，请检查桥接是否运行及地址是否正确", retryable=not sending
            ) from exc
        except (httpx.ProtocolError, httpx.UnsupportedProtocol, httpx.DecodingError) as exc:
            raise BridgeError("QQ 桥接通信格式无效，请检查连接设置", uncertain=sending) from exc
        except httpx.RequestError as exc:
            raise BridgeError(
                "QQ 桥接请求中断或超时，请核对消息是否已经送达",
                uncertain=sending,
                retryable=not sending,
            ) from exc

        if not 200 <= response.status_code < 300:
            # A server error or request timeout may occur after a successful send.
            definite_rejections = {400, 401, 403, 404, 405, 406, 413, 415, 422, 429}
            uncertain = sending and response.status_code not in definite_rejections
            raise BridgeError(
                f"QQ 桥接返回 HTTP {response.status_code}，请检查桥接配置",
                uncertain=uncertain,
                retryable=not sending
                and (response.status_code in {408, 429} or response.status_code >= 500),
            )
        try:
            body = response.json()
            if not isinstance(body, dict):
                raise ValueError("not a OneBot object")
        except (ValueError, TypeError) as exc:
            raise BridgeError("QQ 桥接响应格式无效，请核对消息状态", uncertain=sending) from exc

        status, retcode = body.get("status"), body.get("retcode")
        if status == "failed" and type(retcode) is int and retcode not in {0, 1}:
            # OneBot has no universal not-logged-in error code. Only the login
            # readiness query can treat a valid failed envelope as not ready;
            # never infer this from a provider's free-form error text.
            raise BridgeError(
                "QQ 桥接拒绝了请求，请检查桥接日志",
                retryable=not sending and action == "get_login_info",
            )
        if status != "ok" or type(retcode) is not int or retcode != 0:
            raise BridgeError("QQ 桥接未确认操作成功，请核对消息状态", uncertain=sending)
        return body.get("data")

    async def send_private(self, base_url: str, token: str, user_id: str, text: str) -> str:
        """Send a single plain-text private message and return its external ID."""
        if not str(user_id).isdigit() or int(user_id) <= 0:
            raise BridgeError("联系人 QQ 号无效")
        if not isinstance(text, str) or not text.strip():
            raise BridgeError("不能发送空消息")
        data = await self._call(
            base_url,
            token,
            "send_private_msg",
            {"user_id": int(user_id), "message": text, "auto_escape": True},
            sending=True,
        )
        message_id = data.get("message_id") if isinstance(data, dict) else None
        if (
            isinstance(message_id, bool)
            or not isinstance(message_id, (str, int))
            or not str(message_id).removeprefix("-").isdigit()
        ):
            raise BridgeError("QQ 桥接未返回消息编号，请核对消息是否已经送达", uncertain=True)
        return str(message_id)

    async def friends(self, base_url: str, token: str) -> list[dict[str, str]]:
        data = await self._call(base_url, token, "get_friend_list", {}, sending=False)
        if not isinstance(data, list):
            raise BridgeError("QQ 桥接未返回有效好友列表")
        result = []
        for friend in data:
            if not isinstance(friend, dict) or not str(friend.get("user_id", "")).isdigit():
                raise BridgeError("QQ 桥接好友列表格式无效")
            qq_id = str(friend["user_id"])
            name = friend.get("remark") or friend.get("nickname") or qq_id
            result.append({"qq_id": qq_id, "name": str(name)})
        return result

    async def login_info(self, base_url: str, token: str) -> str:
        """Return the bridge's current account for an explicit pre-send check."""
        data = await self._call(base_url, token, "get_login_info", {}, sending=False)
        user_id = data.get("user_id") if isinstance(data, dict) else None
        if data is None or (
            isinstance(data, dict)
            and (
                user_id is None
                or user_id == ""
                or (type(user_id) is int and user_id == 0)
                or (isinstance(user_id, str) and user_id == "0")
            )
        ):
            raise BridgeError("QQ 桥接未返回有效登录账号，正在等待 QQ 登录", retryable=True)
        if (
            isinstance(user_id, bool)
            or not isinstance(user_id, (str, int))
            or not str(user_id).isascii()
            or not str(user_id).isdigit()
            or int(user_id) <= 0
        ):
            raise BridgeError("QQ 桥接未返回有效登录账号，已停止发送，请检查桥接登录状态")
        return str(user_id)
