## Context

The Agent service already has four distinct persistence domains: Agent sessions, knowledge catalog, quality data, and long-term memory. Feishu bot messages already identify users by the configured application's `open_id`, but browser requests are anonymous and the memory API currently trusts an `X-Authenticated-User-ID` header. The service runs on one fixed intranet host with SQLite, Chroma, and one Uvicorn worker. Administrator authentication is a separate server-side session system and must remain isolated.

The feature crosses API middleware, OAuth, persistence, memory ownership, Agent conversations, Feishu identity, and Vue navigation. It therefore uses a dedicated `storage/user_auth.db` rather than coupling user identity migrations to the catalog or memory schema.

## Goals / Non-Goals

**Goals:**

- Keep chat and device-scoped memory usable without login.
- Let a user optionally sign in through the existing Feishu self-built application and share one `open_id` identity across web, Feishu bot, and Codex.
- Replace caller-controlled identity headers with server-resolved identity.
- Require explicit confirmation before anonymous data is merged into a Feishu identity.
- Provide least-privilege, named, revocable, show-once personal API tokens for Codex.
- Preserve current Agent, Bug Graph, RAG, quality, memory, Feishu bot, and administrator behavior.

**Non-Goals:**

- Mandatory login, public internet OAuth, multi-tenant federation, or employee directory synchronization.
- Fine-grained business authorization, business write tools, administrator access through personal tokens, or token-based memory deletion.
- Persisting Feishu OAuth credentials, raw OAuth responses, email, phone, or employee number.
- Redis, PostgreSQL, multiple Uvicorn workers, or cross-server session replication.

## Decisions

### Dedicated auth database and repository

`storage/user_auth.db` owns schema migrations, anonymous devices, Feishu users, OAuth states, user sessions, personal token hashes, conversation ownership, merge jobs, and auth audit events. WAL, foreign keys, UTC timestamps, explicit transactions, and idempotent migrations follow existing repository patterns. A separate database limits coupling and allows rollback without rewriting memory or catalog schemas.

Alternative considered: adding tables to `agent_memory.db`. Rejected because authentication lifecycle and retention differ from memory, and auth must remain available when memory is disabled.

### Server-resolved identity with fixed precedence

A focused resolver evaluates `Authorization: Bearer`, then the Feishu session cookie, then the anonymous cookie. Invalid bearer credentials fail with `401` instead of silently falling back. Invalid or expired user sessions fall through to anonymous identity and clear their cookie. If no valid anonymous cookie exists, the resolver creates a 32-byte random secret, persists only SHA-256, and emits an HttpOnly, SameSite=Strict cookie with a 180-day sliding expiry. Owner IDs use `anon:<uuid>`; Feishu and token identities use the Feishu `open_id` unchanged.

Alternative considered: retaining `X-Authenticated-User-ID` for trusted callers. Rejected because the public API cannot prove which callers are trusted and the header permits memory impersonation.

### OAuth code exchange stays server-side

`GET /api/v1/auth/feishu/start` creates a single-use, short-lived state record tied to the current anonymous owner and redirects to Feishu. The callback validates state, exchanges the code server-side, fetches the current user, optionally validates a configured enterprise tenant identifier, stores only stable profile fields (`open_id`, display name, avatar URL, tenant key), creates a random server-side session, and redirects to the merge preview or chat. When no tenant key is configured, every user authorized by the application may log in. OAuth codes, access/refresh tokens, and raw responses are never persisted or logged. The callback URI is fixed to `http://172.18.26.1:8000/api/v1/auth/feishu/callback` by configuration.

Alternative considered: OAuth entirely in the SPA. Rejected because it would expose application credentials and access tokens to browser storage.

### Separate user and administrator sessions

The user cookie and CSRF token are independent from `knowledge_admin`. Authenticated browser write endpoints require the user session plus its own `X-User-CSRF-Token`; administrator APIs continue using their existing session and `X-CSRF-Token`. Bearer-token requests do not use browser CSRF and are scope-checked instead.

### Explicit, transactional merge with conflict demotion

The callback never mutates anonymous data. It records the source anonymous owner in the session and exposes a merge preview. Confirmation creates an idempotent merge job and moves conversation ownership, conversation summaries, extraction jobs, candidates, and non-conflicting confirmed memories to the Feishu owner. Exact normalized duplicates deduplicate. If an anonymous confirmed memory conflicts with an existing Feishu memory of the same scope/domain/type/subject, the Feishu memory remains confirmed and the anonymous fact becomes a pending candidate. After success, the anonymous device is disabled and its cookie cleared. Declining or ignoring the preview keeps anonymous data untouched while logged-in activity starts in a new conversation.

Because memory and auth use separate SQLite files, the merge service uses a recoverable job with ordered idempotent steps instead of pretending to have a cross-database transaction. A failed job remains retryable and does not disable the anonymous identity.

### Personal tokens are opaque capabilities

Tokens contain a public identifier plus 32 random bytes. The full value is returned only from create, while SHA-256 and a short non-secret prefix are stored. Tokens have a name, fixed allowed scopes, creation time, optional revocation time, and `last_used_at`; they have no automatic expiry by the approved product decision. Revocation is immediate. Token creation/list/revocation requires a Feishu user session and user CSRF, never another personal token.

Alternative considered: JWTs. Rejected because revocation, show-once semantics, and server-side scope changes are simpler and more reliable with opaque tokens.

### Conversation ownership is enforced at the API boundary

Every web conversation is bound to the resolved owner on first use. Reusing a conversation ID under another owner returns `404` to avoid existence disclosure. Feishu bot conversation IDs are bound to the sender `open_id`; Codex requests resolve through the bearer token. New conversations after login do not inherit an unmerged anonymous conversation.

### Vue remains usable before authentication

The client loads `/api/v1/auth/me` on startup. It displays “当前设备” for anonymous identity and the Feishu profile when authenticated, offers optional login, and adds `/account` for personal token management. Token plaintext is displayed in a one-time modal with an explicit warning. The `/memory` page uses the current resolved identity in both states.

## Risks / Trade-offs

- [HTTP callback and cookies on an intranet host] -> Keep cookie `Secure` configurable and default it off for the approved HTTP LAN deployment; document that production HTTPS must enable it.
- [Changing LAN address breaks OAuth] -> Make the public base URL and callback configurable, validate them at startup, and retain only the approved current LAN address.
- [Cross-database merge interruption] -> Persist an idempotent merge job and disable the anonymous device only after every step succeeds.
- [Long-lived personal token theft] -> Store only hashes, show once, redact authorization headers, scope narrowly, expose last use, and support immediate revocation.
- [Feishu profile or tenant endpoint changes] -> Isolate Feishu HTTP calls behind a client interface with strict response validation and deterministic tests.
- [Anonymous cookie deletion loses discoverability] -> The server cannot recover an unlinked anonymous identity; login merge is the supported ownership upgrade path.
- [Multiple identities on a shared browser] -> Logout removes the Feishu session and issues a fresh anonymous identity rather than reviving a merged/disabled device.

## Migration Plan

1. Add configuration with user auth disabled by default until Feishu OAuth settings are complete.
2. Create and validate `user_auth.db` migrations without touching existing memory or Agent databases.
3. Enable anonymous identity resolution and conversation binding; existing unowned conversations become bound on their next successful request.
4. Enable Feishu OAuth, verify the fixed LAN callback from an employee browser, then enable the Vue login controls.
5. Enable merge preview/confirmation and test exact duplicate, conflict, retry, and decline paths against fixture databases.
6. Enable personal token creation and Codex access after redaction and scope tests pass.
7. Before restart, ensure source sync, evaluation, and memory queues have no queued/running work; restart one Uvicorn worker and verify readiness.

Rollback disables user auth endpoints and resolver integration, restores anonymous-only web ownership, and leaves `user_auth.db` intact for later recovery. No existing memory rows are deleted during rollback.

## Open Questions

None. The callback host, optional-login behavior, explicit merge confirmation, personal binding, token lifetime, and token scopes were confirmed by the user.
