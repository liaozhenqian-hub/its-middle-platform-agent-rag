# Feishu OAuth Separate Credentials Design

## Goal

Keep the existing formal-enterprise Feishu bot online while using the new test-enterprise application for optional web login. The two integrations must not overwrite or implicitly reuse each other's credentials when dedicated OAuth credentials are configured.

## Configuration

Add two optional environment variables:

```dotenv
FEISHU_OAUTH_APP_ID=
FEISHU_OAUTH_APP_SECRET=
```

Credential resolution is deterministic:

1. The Feishu bot always uses `FEISHU_APP_ID` and `FEISHU_APP_SECRET`.
2. Web OAuth uses `FEISHU_OAUTH_APP_ID` and `FEISHU_OAUTH_APP_SECRET` when both are configured.
3. Web OAuth falls back to `FEISHU_APP_ID` and `FEISHU_APP_SECRET` only when neither dedicated OAuth value is configured, preserving backward compatibility.
4. Supplying only one member of either credential pair is a startup configuration error.

`FEISHU_TENANT_KEY` remains optional. A blank value accepts every user that the selected OAuth application can authorize. For the current test application, that means users in the test enterprise's published availability range, not all users in the formal enterprise.

## Runtime Boundaries

- The bot startup path and long-connection client remain unchanged and continue reading the shared bot credential pair.
- `UserAuthService` constructs `FeishuOAuthClient` from resolved OAuth credentials instead of bot credentials.
- OAuth availability depends on the resolved OAuth pair, `USER_AUTH_ENABLED`, and `FEISHU_OAUTH_ENABLED`.
- App IDs, App Secrets, authorization codes, states, and access tokens are not logged or returned by readiness endpoints.

## Deployment

The local `.env` keeps the existing formal bot values and adds the test application values:

```dotenv
FEISHU_BOT_ENABLED=true
FEISHU_APP_ID=<existing-formal-bot-app-id>
FEISHU_APP_SECRET=<existing-formal-bot-secret>
FEISHU_OAUTH_ENABLED=true
FEISHU_OAUTH_APP_ID=<test-enterprise-login-app-id>
FEISHU_OAUTH_APP_SECRET=<test-enterprise-login-secret>
FEISHU_TENANT_KEY=
```

The service is restarted with one Uvicorn worker after queues are idle. Verification checks that the bot remains available, the OAuth authorization URL uses the test application, the callback creates a Feishu user session, and `/api/v1/auth/me` reports `identity_kind=feishu`.

## Tests

- Dedicated OAuth credentials take precedence over bot credentials.
- Omitting dedicated OAuth credentials preserves the existing fallback behavior.
- Partial dedicated OAuth credentials fail settings validation without exposing values.
- Bot validation still depends only on the formal bot credential pair.
- `UserAuthService` passes the resolved OAuth pair to `FeishuOAuthClient`.
- Existing bot, OAuth, settings, lifespan, and user-auth API tests remain green.
