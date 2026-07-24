from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlencode

import httpx


class FeishuOAuthError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class FeishuOAuthProfile:
    open_id: str
    tenant_key: str
    display_name: str
    avatar_url: str | None = None


class FeishuOAuthClient:
    AUTHORIZE_URL = "https://open.feishu.cn/open-apis/authen/v1/authorize"
    TOKEN_URL = "https://open.feishu.cn/open-apis/authen/v2/oauth/token"
    USER_INFO_URL = "https://open.feishu.cn/open-apis/authen/v1/user_info"

    def __init__(
        self,
        *,
        app_id: str,
        app_secret: str,
        callback_url: str,
        tenant_key: str,
        http_client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 15.0,
    ):
        self.app_id = _required(app_id, "app_id")
        self.app_secret = _required(app_secret, "app_secret")
        self.callback_url = _required(callback_url, "callback_url")
        self.tenant_key = str(tenant_key).strip()
        self._http_client = http_client
        self.timeout_seconds = timeout_seconds

    def authorization_url(self, state: str) -> str:
        query = urlencode(
            {
                "app_id": self.app_id,
                "redirect_uri": self.callback_url,
                "state": _required(state, "state"),
            }
        )
        return f"{self.AUTHORIZE_URL}?{query}"

    async def authenticate(self, code: str) -> FeishuOAuthProfile:
        client = self._http_client or httpx.AsyncClient(timeout=self.timeout_seconds)
        owns_client = self._http_client is None
        try:
            try:
                exchange = await client.post(
                    self.TOKEN_URL,
                    json={
                        "grant_type": "authorization_code",
                        "client_id": self.app_id,
                        "client_secret": self.app_secret,
                        "code": _required(code, "code"),
                        "redirect_uri": self.callback_url,
                    },
                )
                exchange.raise_for_status()
                exchange_data = _token_response_data(exchange)
                access_token = str(
                    exchange_data.get("access_token")
                    or exchange_data.get("user_access_token")
                    or ""
                ).strip()
                if not access_token:
                    raise FeishuOAuthError("Feishu OAuth token response is invalid")
                profile_response = await client.get(
                    self.USER_INFO_URL,
                    headers={"Authorization": f"Bearer {access_token}"},
                )
                profile_response.raise_for_status()
                profile_data = _response_data(profile_response)
            except FeishuOAuthError:
                raise
            except (httpx.HTTPError, ValueError, TypeError) as exc:
                raise FeishuOAuthError("Feishu OAuth request failed") from exc

            tenant_key = str(profile_data.get("tenant_key") or "").strip()
            if self.tenant_key and tenant_key != self.tenant_key:
                raise FeishuOAuthError("Feishu OAuth tenant is not allowed")
            open_id = str(profile_data.get("open_id") or "").strip()
            display_name = str(
                profile_data.get("name") or profile_data.get("en_name") or ""
            ).strip()
            if not open_id or not display_name:
                raise FeishuOAuthError("Feishu OAuth profile is invalid")
            avatar_url = str(profile_data.get("avatar_url") or "").strip() or None
            return FeishuOAuthProfile(
                open_id=open_id,
                tenant_key=tenant_key,
                display_name=display_name,
                avatar_url=avatar_url,
            )
        finally:
            if owns_client:
                await client.aclose()


def _response_data(response: httpx.Response) -> dict[str, object]:
    payload = response.json()
    if not isinstance(payload, dict) or int(payload.get("code", -1)) != 0:
        raise FeishuOAuthError("Feishu OAuth response is invalid")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise FeishuOAuthError("Feishu OAuth response is invalid")
    return data


def _token_response_data(response: httpx.Response) -> dict[str, object]:
    payload = response.json()
    if not isinstance(payload, dict) or int(payload.get("code", -1)) != 0:
        raise FeishuOAuthError("Feishu OAuth response is invalid")
    data = payload.get("data")
    return data if isinstance(data, dict) else payload


def _required(value: str, name: str) -> str:
    normalized = str(value).strip()
    if not normalized:
        raise ValueError(f"{name} is required")
    return normalized
