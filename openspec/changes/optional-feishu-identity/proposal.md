## Why

The web client currently has no trustworthy user identity, while Feishu bot users already have stable `open_id` values and long-term memory is keyed by an owner identifier. Optional identity is needed so anonymous browsing remains frictionless, authenticated web sessions can share memory with the Feishu bot, and Codex can query the same personal context without trusting caller-supplied identity headers.

## What Changes

- Add a server-issued anonymous device identity stored in an HttpOnly cookie, with only its hash persisted.
- Add optional Feishu OAuth login for users authorized by the configured application, with an optional tenant restriction, using `http://172.18.26.1:8000/api/v1/auth/feishu/callback` as the browser callback.
- Add explicit preview and confirmation before merging anonymous conversations and memory into a Feishu identity.
- Add named, revocable personal API tokens for Codex with only `agent:query` and `memory:read` scopes.
- Resolve request identity by strict precedence: personal bearer token, Feishu user session, then anonymous device cookie.
- Bind web conversations to their resolved owner and remove trust in `X-Authenticated-User-ID` from public requests.
- Add optional login state, merge confirmation, and personal token management to the Vue client while keeping anonymous chat available.
- Keep administrator authentication and CSRF handling independent from user authentication.

## Capabilities

### New Capabilities
- `optional-user-identity`: Anonymous device identity, Feishu OAuth login, user sessions, request identity resolution, and conversation ownership.
- `anonymous-identity-merge`: Previewed and explicitly confirmed migration of anonymous conversations and memory into a Feishu identity.
- `personal-api-tokens`: Named, show-once, hashed and revocable personal tokens that let Codex query the Agent and read memory as the bound Feishu user.

### Modified Capabilities

None.

## Impact

- Adds `storage/user_auth.db` and a focused `knowledge/auth/` package for persistence, identity resolution, OAuth, merge, and token services.
- Adds public auth/account endpoints under `/api/v1/auth` and `/api/v1/account` and integrates identity into chat and memory APIs.
- Extends FastAPI lifespan/readiness and configuration for user auth, cookies, Feishu OAuth, and token hashing.
- Extends the Vue header, `/memory`, and a new `/account` route without changing `/admin` authentication.
- Reuses the existing Feishu application and memory repository; no Redis, PostgreSQL, mandatory login, or business write permission is introduced.
