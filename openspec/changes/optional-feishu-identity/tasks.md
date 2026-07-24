## 1. Auth Storage And Configuration

- [x] 1.1 Add failing settings tests for auth DB paths, cookie/session lifetimes, Feishu public callback, tenant restriction, and validation, then implement the new Settings fields and resolved paths.
- [x] 1.2 Add failing repository migration tests for WAL, foreign keys, idempotency, and all auth tables, then implement `knowledge/auth/models.py` and `knowledge/auth/repository.py`.
- [x] 1.3 Add repository behavior tests for anonymous devices, OAuth states, Feishu users, user sessions, conversation ownership, merge jobs, audit events, and expired-record cleanup, then implement those operations.

## 2. Identity Resolution

- [x] 2.1 Add failing tests for anonymous cookie issuance, hash-only persistence, stable owner reuse, sliding expiry, disabled devices, and fresh identity after logout, then implement anonymous identity handling.
- [x] 2.2 Add failing resolver tests for bearer/session/anonymous precedence, invalid bearer failure, expired session fallback, caller-supplied identity rejection, and scope enforcement, then implement the request identity resolver.
- [x] 2.3 Add failing conversation ownership tests for first binding, same-owner reuse, cross-owner 404, Feishu sender ownership, and deletion checks, then integrate ownership enforcement with Agent sessions.

## 3. Feishu OAuth And User Sessions

- [x] 3.1 Add failing Feishu OAuth client tests for authorization URL, code exchange, profile normalization, tenant validation, response redaction, and transport errors, then implement the isolated client.
- [x] 3.2 Add failing auth route tests for start, callback, one-use/expired state, session creation, seven-day sliding and thirty-day absolute expiry, `/auth/me`, independent user CSRF, and logout, then implement the routes.
- [x] 3.3 Extend lifespan/readiness tests, initialize and close auth services without preventing degraded startup, and expose a non-sensitive auth readiness status.

## 4. Anonymous Identity Merge

- [x] 4.1 Add failing memory repository tests for owner inventory, exact duplicate detection, conflict conversion, owner transfer, and idempotent updates, then implement focused merge primitives.
- [x] 4.2 Add failing merge service tests for preview-only behavior, explicit confirmation, ordered recoverable jobs, retry after failure, duplicate/conflict handling, anonymous disablement only after success, and decline isolation, then implement the service.
- [x] 4.3 Add failing API tests for merge preview and CSRF-protected confirmation/decline, then expose the merge endpoints without allowing bearer-token management.

## 5. Personal Codex Tokens

- [x] 5.1 Add failing repository/service tests for cryptographic token generation, show-once plaintext, hash-only storage, allowed scopes, unique names, no automatic expiry, last-use tracking, and immediate revocation, then implement token management.
- [x] 5.2 Add failing API tests for Feishu-session-only create/list/revoke, user CSRF, redacted responses, `agent:query` and `memory:read` authorization, forbidden admin/write/delete/merge calls, and `X-Client-Channel: codex`, then expose the account routes.
- [x] 5.3 Add log/quality/audit redaction regression tests proving bearer values, hashes, OAuth codes, and OAuth tokens are never persisted or emitted.

## 6. Chat, Memory, And Feishu Integration

- [x] 6.1 Replace `X-Authenticated-User-ID` in chat JSON/SSE and memory APIs with the identity resolver, add failing tests for anonymous, Feishu session, Codex token, required scopes, and response compatibility, then implement the integration.
- [x] 6.2 Bind memory recall/extraction and conversation summaries to the resolved owner while preserving existing domain/team memory behavior; add regression tests for web/bot/Codex shared Feishu memory and anonymous isolation.
- [x] 6.3 Bind Feishu bot conversations to sender `open_id` through the ownership repository and add regression tests for sender isolation and existing thread behavior.

## 7. Vue Optional Identity Experience

- [x] 7.1 Add failing API/store tests for `/auth/me`, login redirect, logout, merge preview/confirmation/decline, user CSRF, and personal token create/list/revoke, then implement the typed clients and Pinia user identity store.
- [x] 7.2 Add failing component tests and implement a responsive identity header showing “当前设备” or the Feishu profile, optional login, merge prompt, and logout without affecting admin navigation.
- [x] 7.3 Add failing route/view tests and implement `/account` token management with named scopes, one-time plaintext display, copy action, last-use display, and immediate revoke.
- [x] 7.4 Update `/memory` tests and UI so anonymous device memory and Feishu memory both load through server identity without an identity-required dead end.

## 8. Verification And Rollout

- [x] 8.1 Add non-secret environment examples/documentation for auth settings and the exact `172.18.26.1` callback, ensuring obsolete LAN addresses are absent.
- [x] 8.2 Run strict OpenSpec validation, the full Python suite, Vitest, Vue production build, and Playwright identity flows; fix all failures and re-run each command fresh.
- [x] 8.3 Confirm source sync, evaluation, and memory queues have no queued/running jobs, restart one Uvicorn worker, and verify anonymous chat, Feishu login readiness, Codex scope handling, memory, catalog, worker, model, Chroma, MCP, Grafana, and Bug Graph health.
- [x] 8.4 Make tenant restriction optional, enable OAuth by default when complete Feishu application credentials exist, and cover unrestricted login with regression tests.
