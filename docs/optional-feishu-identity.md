# Optional Feishu Identity

Web login is optional. A browser without a Feishu session receives an HttpOnly anonymous device cookie and can use chat plus device-scoped memory immediately.

## Feishu Configuration

Web login can use a dedicated self-built Feishu application without replacing the existing bot application. Configure the web-login application's developer console redirect URI exactly as:

```text
http://172.18.26.1:8000/api/v1/auth/feishu/callback
```

Set the following values locally. Do not place real credentials in source control:

```dotenv
USER_AUTH_ENABLED=true
USER_AUTH_DB=storage/user_auth.db
USER_PUBLIC_BASE_URL=http://172.18.26.1:8000
FEISHU_OAUTH_ENABLED=true
FEISHU_OAUTH_APP_ID=<fill-locally>
FEISHU_OAUTH_APP_SECRET=<fill-locally>
FEISHU_TENANT_KEY=
USER_COOKIE_SECURE=false
```

The Feishu bot always uses `FEISHU_APP_ID` and `FEISHU_APP_SECRET`. OAuth prefers the dedicated `FEISHU_OAUTH_APP_ID` and `FEISHU_OAUTH_APP_SECRET` pair. When both dedicated values are blank, OAuth falls back to the bot pair for backward compatibility. Each pair must be configured completely; a partial pair prevents startup.

Keep real credential values only in the local `.env`. Do not paste them into chat, logs, documentation, or source control.

`USER_COOKIE_SECURE=false` is required only for the current HTTP intranet address. Change the service to HTTPS and set it to `true` before exposing the application beyond the approved LAN.

Leave `FEISHU_TENANT_KEY` blank to allow every Feishu user that this application can authorize. Set it only when login must be restricted to one tenant.

The callback is a browser redirect after the employee authorizes the application. It is not the Feishu bot webhook or long-connection endpoint.

## Identity Behavior

Identity precedence is personal Bearer Token, Feishu user Session, then anonymous device Cookie. Public APIs ignore `X-Authenticated-User-ID`.

After Feishu login, the web user and Feishu bot share the same application-level `open_id`. Anonymous memory and conversations are not moved automatically; the page first displays a merge preview and requires explicit confirmation.

## Codex Personal Token

After Feishu login, open `/account`, create a named Token, and store the one-time value in the local Codex configuration. A Codex request uses:

```http
Authorization: Bearer <personal-token>
X-Client-Channel: codex
```

The allowed scopes are `agent:query` and `memory:read`. A personal Token cannot access administrator APIs, approve write tools, delete memory, merge identities, log out browser sessions, or manage Tokens. Tokens have no automatic expiry, so revoke unused Tokens from `/account` immediately.
