import json

import httpx
import pytest

from qecho.adapters import BridgeClient, BridgeError, ModelClient, ProviderError


async def model_result(handler, **patch):
    settings = {
        "model_mode": "live",
        "model_base_url": "https://model.example/v1/",
        "model_name": "my-model",
        "temperature": 0.6,
        "max_reply_chars": 100,
    }
    settings.update(patch)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        return await ModelClient(client).generate(
            settings, [{"role": "user", "content": "你好"}], "secret-key"
        )


@pytest.mark.asyncio
async def test_model_request_and_plain_text_response():
    def handler(request):
        assert str(request.url) == "https://model.example/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer secret-key"
        payload = json.loads(request.content)
        assert payload["messages"] == [{"role": "user", "content": "你好"}]
        assert payload["model"] == "my-model"
        assert payload["stream"] is False
        return httpx.Response(200, json={"choices": [{"message": {"content": "  好呀！  "}}]})

    assert await model_result(handler) == "好呀！"


@pytest.mark.asyncio
async def test_demo_never_uses_network():
    def handler(request):
        pytest.fail("demo mode must not connect")

    reply = await model_result(handler, model_mode="demo", model_base_url="")
    assert reply.startswith("[演练模型]")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "base_url",
    ["file:///tmp/model", "https://key:secret@model.example", "https://model.example?key=secret"],
)
async def test_model_rejects_invalid_endpoint_before_network(base_url):
    def handler(request):
        pytest.fail("invalid model URLs must not connect")

    with pytest.raises(ProviderError):
        await model_result(handler, model_base_url=base_url)


@pytest.mark.asyncio
async def test_model_does_not_follow_redirect_or_expose_response():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(307, headers={"location": "https://other.example"}, text="secret-key")

    with pytest.raises(ProviderError) as error:
        await model_result(handler)
    assert len(requests) == 1
    assert "secret-key" not in str(error.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response", [{}, {"choices": []}, {"choices": [{"message": {"content": " "}}]}, []]
)
async def test_model_rejects_empty_or_malformed_response(response):
    with pytest.raises(ProviderError):
        await model_result(lambda request: httpx.Response(200, json=response))


@pytest.mark.asyncio
async def test_model_network_errors_are_sanitized():
    def handler(request):
        raise httpx.ReadTimeout("secret-key", request=request)

    with pytest.raises(ProviderError) as error:
        await model_result(handler)
    assert "secret-key" not in str(error.value)


@pytest.mark.asyncio
async def test_model_does_not_silently_truncate_long_reply():
    content = "这是一条完整的承诺：我不能答应。"
    with pytest.raises(ProviderError, match="超过长度限制"):
        await model_result(
            lambda request: httpx.Response(
                200, json={"choices": [{"message": {"content": content}}]}
            ),
            max_reply_chars=12,
        )


@pytest.mark.asyncio
async def test_send_uses_plain_text_and_returns_message_id():
    def handler(request):
        assert request.url.path == "/send_private_msg"
        assert request.headers["authorization"] == "Bearer bridge-secret"
        assert json.loads(request.content) == {
            "user_id": 123456,
            "message": "[CQ:at,qq=all]",
            "auto_escape": True,
        }
        return httpx.Response(
            200, json={"status": "ok", "retcode": 0, "data": {"message_id": -123}}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert (
            await BridgeClient(client).send_private(
                "http://127.0.0.1:3000", "bridge-secret", "123456", "[CQ:at,qq=all]"
            )
            == "-123"
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,body,uncertain",
    [
        (200, {"status": "failed", "retcode": 100, "message": "bridge-secret"}, False),
        (200, {"status": "async", "retcode": 1, "data": None}, True),
        (200, {"status": "ok", "retcode": 0, "data": {}}, True),
        (200, {"status": "ok", "retcode": 0, "data": {"message_id": "not-an-id"}}, True),
        (200, {"status": "ok", "retcode": False, "data": {"message_id": 123}}, True),
        (200, [], True),
        (401, {}, False),
        (503, {}, True),
        (408, {}, True),
        (307, {}, True),
    ],
)
async def test_bridge_failure_classification(status, body, uncertain):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(status, json=body))
    ) as client:
        with pytest.raises(BridgeError) as error:
            await BridgeClient(client).send_private(
                "http://localhost:3000", "bridge-secret", "123", "hi"
            )
    assert error.value.uncertain is uncertain
    assert error.value.retryable is False
    assert "bridge-secret" not in str(error.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "exception,uncertain",
    [(httpx.ConnectError, False), (httpx.ReadTimeout, True), (httpx.WriteError, True)],
)
async def test_bridge_network_failure_never_retries(exception, uncertain):
    requests = []

    def handler(request):
        requests.append(request)
        raise exception("bridge-secret", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(BridgeError) as error:
            await BridgeClient(client).send_private(
                "http://localhost:3000", "bridge-secret", "123", "hi"
            )
    assert error.value.uncertain is uncertain
    assert error.value.retryable is False
    assert len(requests) == 1
    assert "bridge-secret" not in str(error.value)


@pytest.mark.asyncio
async def test_bridge_does_not_follow_redirects_even_if_client_default_does():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(307, headers={"location": "http://other.example/send_private_msg"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), follow_redirects=True
    ) as client:
        with pytest.raises(BridgeError):
            await BridgeClient(client).send_private("http://localhost:3000", "secret", "123", "hi")
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_friends_prefer_remark_and_do_not_create_contacts():
    def handler(request):
        assert request.url.path == "/get_friend_list"
        return httpx.Response(
            200,
            json={
                "status": "ok",
                "retcode": 0,
                "data": [
                    {"user_id": 123, "nickname": "昵称", "remark": "备注"},
                    {"user_id": 456, "nickname": "好友"},
                ],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await BridgeClient(client).friends("http://localhost:3000", "") == [
            {"qq_id": "123", "name": "备注"},
            {"qq_id": "456", "name": "好友"},
        ]


@pytest.mark.asyncio
async def test_close_preserves_injected_client():
    async with httpx.AsyncClient() as client:
        await ModelClient(client).close()
        await BridgeClient(client).close()
        assert not client.is_closed


@pytest.mark.asyncio
@pytest.mark.parametrize("user_id", [123456, "123456"])
async def test_login_info_returns_bridge_account_as_string(user_id):
    def handler(request):
        assert request.url.path == "/get_login_info"
        assert request.headers["authorization"] == "Bearer bridge-secret"
        assert json.loads(request.content) == {}
        return httpx.Response(
            200, json={"status": "ok", "retcode": 0, "data": {"user_id": user_id}}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert (
            await BridgeClient(client).login_info("http://localhost:3000", "bridge-secret")
            == "123456"
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "data,retryable",
    [
        ({}, True),
        (None, True),
        ([], False),
        ({"user_id": True}, False),
        ({"user_id": "abc"}, False),
        ({"user_id": -1}, False),
        ({"user_id": 0}, True),
        ({"user_id": 1.5}, False),
        ({"user_id": "１２３４５６"}, False),
    ],
)
async def test_login_info_rejects_missing_or_invalid_account(data, retryable):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"status": "ok", "retcode": 0, "data": data})
        )
    ) as client:
        with pytest.raises(BridgeError, match="有效登录账号") as error:
            await BridgeClient(client).login_info("http://localhost:3000", "bridge-secret")
    assert error.value.uncertain is False
    assert error.value.retryable is retryable


@pytest.mark.asyncio
async def test_login_info_failure_is_definite_and_does_not_send():
    requests = []

    def handler(request):
        requests.append(request.url.path)
        return httpx.Response(200, json={"status": "failed", "retcode": 100, "message": "secret"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(BridgeError) as error:
            await BridgeClient(client).login_info("http://localhost:3000", "bridge-secret")
    assert error.value.uncertain is False
    assert error.value.retryable is True
    assert requests == ["/get_login_info"]


def test_bridge_error_retryability_is_independent_of_delivery_uncertainty():
    assert BridgeError("failure").retryable is False
    assert BridgeError("delivery unknown", uncertain=True).retryable is False
    assert BridgeError("login not ready", retryable=True).uncertain is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,retryable",
    [
        (401, False),
        (403, False),
        (404, False),
        (408, True),
        (429, True),
        (500, True),
        (503, True),
        (307, False),
    ],
)
async def test_readonly_http_errors_distinguish_configuration_from_readiness(status, retryable):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(status, json={}))
    ) as client:
        with pytest.raises(BridgeError) as error:
            await BridgeClient(client).login_info("http://localhost:3000", "bridge-secret")
    assert error.value.retryable is retryable
    assert error.value.uncertain is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "exception,retryable",
    [
        (httpx.ConnectError, True),
        (httpx.ConnectTimeout, True),
        (httpx.PoolTimeout, True),
        (httpx.ReadTimeout, True),
        (httpx.WriteError, True),
        (httpx.RemoteProtocolError, False),
        (httpx.LocalProtocolError, False),
        (httpx.DecodingError, False),
    ],
)
async def test_readonly_network_errors_are_classified_without_internal_retries(
    exception, retryable
):
    requests = []

    def handler(request):
        requests.append(request)
        raise exception("bridge-secret", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(BridgeError) as error:
            await BridgeClient(client).login_info("http://localhost:3000", "bridge-secret")
    assert error.value.retryable is retryable
    assert error.value.uncertain is False
    assert len(requests) == 1
    assert "bridge-secret" not in str(error.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [b"not JSON", b"[]", b"{}"])
async def test_readonly_malformed_envelopes_are_not_retried(body):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=body))
    ) as client:
        with pytest.raises(BridgeError) as error:
            await BridgeClient(client).login_info("http://localhost:3000", "bridge-secret")
    assert error.value.retryable is False


@pytest.mark.asyncio
async def test_onebot_business_failure_is_retryable_only_for_login_readiness_query():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={"status": "failed", "retcode": 100, "message": "not used for classification"},
            )
        )
    ) as client:
        with pytest.raises(BridgeError) as error:
            await BridgeClient(client).friends("http://localhost:3000", "bridge-secret")
    assert error.value.retryable is False
    assert error.value.uncertain is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "file:///bridge",
        "https://secret:secret@bridge.example",
        "http://bridge.example?token=secret",
    ],
)
async def test_readonly_invalid_endpoint_is_not_retried(url):
    def handler(request):
        pytest.fail("invalid endpoints must not connect")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(BridgeError) as error:
            await BridgeClient(client).login_info(url, "bridge-secret")
    assert error.value.retryable is False
