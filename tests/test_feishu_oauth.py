from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from knowledge.auth.oauth import FeishuOAuthClient, FeishuOAuthError


def test_feishu_oauth_authorization_url_contains_fixed_callback_and_state():
    client = FeishuOAuthClient(
        app_id="cli_test",
        app_secret="secret",
        callback_url="http://172.18.26.1:8000/api/v1/auth/feishu/callback",
        tenant_key="tenant-key",
    )

    url = client.authorization_url("opaque-state")
    query = parse_qs(urlparse(url).query)

    assert query["app_id"] == ["cli_test"]
    assert query["state"] == ["opaque-state"]
    assert query["redirect_uri"] == [
        "http://172.18.26.1:8000/api/v1/auth/feishu/callback"
    ]


@pytest.mark.asyncio
async def test_feishu_oauth_exchanges_code_and_normalizes_allowed_profile_fields():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/oauth/token"):
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "access_token": "user-token",
                    "token_type": "Bearer",
                    "expires_in": 7200,
                },
            )
        if request.url.path.endswith("/user_info"):
            assert request.headers["Authorization"] == "Bearer user-token"
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "open_id": "ou_user",
                        "tenant_key": "tenant-key",
                        "name": "User Name",
                        "avatar_url": "https://avatar.example/user.png",
                        "email": "must-not-persist@example.com",
                        "mobile": "must-not-persist",
                    },
                },
            )
        raise AssertionError(request.url)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = FeishuOAuthClient(
            app_id="cli_test",
            app_secret="secret",
            callback_url="http://172.18.26.1:8000/api/v1/auth/feishu/callback",
            tenant_key="tenant-key",
            http_client=http,
        )
        profile = await client.authenticate("one-use-code")

    assert profile.open_id == "ou_user"
    assert profile.tenant_key == "tenant-key"
    assert profile.display_name == "User Name"
    assert profile.avatar_url == "https://avatar.example/user.png"
    assert not hasattr(profile, "email")
    assert len(requests) == 2


@pytest.mark.asyncio
async def test_feishu_oauth_rejects_wrong_tenant_and_redacts_remote_error():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth/token"):
            return httpx.Response(200, json={"code": 0, "data": {"access_token": "sensitive-token"}})
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "open_id": "ou_other",
                    "tenant_key": "other-tenant",
                    "name": "Other User",
                },
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = FeishuOAuthClient(
            app_id="cli_test",
            app_secret="secret",
            callback_url="http://172.18.26.1:8000/api/v1/auth/feishu/callback",
            tenant_key="tenant-key",
            http_client=http,
        )
        with pytest.raises(FeishuOAuthError) as error:
            await client.authenticate("sensitive-code")

    assert "sensitive" not in str(error.value)
    assert "tenant" in str(error.value).lower()


@pytest.mark.asyncio
async def test_feishu_oauth_accepts_any_authorized_user_without_tenant_restriction():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth/token"):
            return httpx.Response(200, json={"code": 0, "data": {"access_token": "user-token"}})
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "open_id": "ou_any_user",
                    "tenant_key": "any-tenant",
                    "name": "Any Feishu User",
                },
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = FeishuOAuthClient(
            app_id="cli_test",
            app_secret="secret",
            callback_url="http://172.18.26.1:8000/api/v1/auth/feishu/callback",
            tenant_key="",
            http_client=http,
        )
        profile = await client.authenticate("code")

    assert profile.open_id == "ou_any_user"
    assert profile.tenant_key == "any-tenant"


@pytest.mark.asyncio
async def test_feishu_oauth_wraps_transport_failure_without_secret_material():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("failed with secret-body", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = FeishuOAuthClient(
            app_id="cli_test",
            app_secret="secret",
            callback_url="http://172.18.26.1:8000/api/v1/auth/feishu/callback",
            tenant_key="tenant-key",
            http_client=http,
        )
        with pytest.raises(FeishuOAuthError) as error:
            await client.authenticate("sensitive-code")

    assert str(error.value) == "Feishu OAuth request failed"
