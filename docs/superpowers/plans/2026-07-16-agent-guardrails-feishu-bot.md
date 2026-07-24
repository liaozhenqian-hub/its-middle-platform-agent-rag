# Agent Guardrails And Feishu Bot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Constrain the Manager to middle-platform business work and connect the existing Agent service to a Feishu bot through long-connection events.

**Architecture:** Keep business-scope policy in `MANAGER_INSTRUCTIONS`. Add an isolated `knowledge/feishu/` package containing deterministic event parsing, SQLite idempotency, reply formatting, an SDK adapter, and a lifecycle bridge. FastAPI lifespan injects the existing `AgentService`; Feishu failures remain non-critical and appear in readiness.

**Tech Stack:** Python 3.11, OpenAI Agents SDK, FastAPI lifespan, `lark-oapi==1.7.1`, asyncio/threading, aiosqlite, pytest/pytest-asyncio.

---

### Task 1: Manager Business Scope Guardrails

**Files:**
- Modify: `knowledge/agent_runtime/agent_factory.py`
- Modify: `tests/test_agent_factory.py`

- [ ] Add a failing assertion that Manager instructions name supported middle-platform work, limit greetings to one sentence plus redirect, reject extended casual conversation, ask for clarification on ambiguous business requests, prohibit writes/permission bypass/sensitive output, and require evidence for internal facts.
- [ ] Run `python -m pytest tests/test_agent_factory.py -q` and verify the new assertion fails.
- [ ] Extend `MANAGER_INSTRUCTIONS` only; do not alter specialist prompts or tool order.
- [ ] Re-run the focused test and verify it passes.

### Task 2: Configuration And Dependency

**Files:**
- Modify: `pyproject.toml`
- Modify: `.env.example`
- Modify: `knowledge/config/settings.py`
- Modify: `tests/test_settings.py`

- [ ] Add failing Settings tests for disabled defaults, required credentials when enabled, project-root event DB resolution, reply length bounds, and positive Agent timeout.
- [ ] Run `python -m pytest tests/test_settings.py -q` and verify failure.
- [ ] Add `lark-oapi==1.7.1` plus `FEISHU_BOT_ENABLED`, `FEISHU_APP_ID`, `FEISHU_APP_SECRET`, `FEISHU_EVENT_DB`, `FEISHU_REPLY_MAX_CHARS`, `FEISHU_GROUP_REQUIRE_MENTION`, and `FEISHU_AGENT_TIMEOUT_SECONDS`.
- [ ] Install the dependency in `.venv-agent` and re-run Settings tests.

### Task 3: Deterministic Event Parsing And Reply Formatting

**Files:**
- Create: `knowledge/feishu/__init__.py`
- Create: `knowledge/feishu/models.py`
- Create: `knowledge/feishu/messages.py`
- Create: `tests/test_feishu_messages.py`

- [ ] Write failing tests for private text, group mention removal, group-without-mention rejection, bot sender rejection, unsupported message type, malformed JSON, empty text, paragraph-aware answer splitting, and public citation summaries.
- [ ] Run `python -m pytest tests/test_feishu_messages.py -q` and verify module import failure.
- [ ] Implement immutable `FeishuIncomingMessage`, `parse_message_event(payload, require_group_mention)`, `format_agent_reply(response)`, and `split_reply(text, max_chars)` without logging or persisting content.
- [ ] Re-run focused tests and verify pass.

### Task 4: SQLite Event Idempotency

**Files:**
- Create: `knowledge/feishu/repository.py`
- Create: `tests/test_feishu_repository.py`

- [ ] Write failing async tests for schema initialization, atomic duplicate claims, completed suppression, one failed-event retry after restart, and absence of message/answer/credential columns and values.
- [ ] Run `python -m pytest tests/test_feishu_repository.py -q` and verify failure.
- [ ] Implement `FeishuEventRepository.initialize()`, `claim()`, `complete()`, and `fail()` with WAL, migration versioning, sanitized error types, and no content fields.
- [ ] Re-run focused tests and verify pass.

### Task 5: Agent Bridge And SDK Boundary

**Files:**
- Create: `knowledge/feishu/gateway.py`
- Create: `knowledge/feishu/bridge.py`
- Create: `tests/test_feishu_bridge.py`

- [ ] Write failing tests proving fixed `middle-platform` scope, `feishu:<chat_id>` conversation mapping, duplicate suppression, timeout handling, sanitized failure reply, sequential chunk replies, and close behavior.
- [ ] Run `python -m pytest tests/test_feishu_bridge.py -q` and verify failure.
- [ ] Define a small `FeishuGateway` protocol and `LarkOapiGateway` adapter. Implement `FeishuBotBridge.start()`, `close()`, synchronous SDK callback handoff with `run_coroutine_threadsafe`, and async `handle_event()` for deterministic testing.
- [ ] Ensure gateway logs contain only event/message IDs, status, exception type, and duration; never credentials, incoming text, answers, citations, or SDK payload bodies.
- [ ] Re-run focused tests and verify pass.

### Task 6: FastAPI Lifespan And Readiness

**Files:**
- Modify: `knowledge/api/app.py`
- Modify: `tests/test_app_lifespan.py`

- [ ] Add failing tests for `feishu_bot=disabled`, available bridge startup/cleanup, and unavailable degradation when credentials or gateway startup fail.
- [ ] Run `python -m pytest tests/test_app_lifespan.py -q` and verify failure.
- [ ] Initialize the repository and bridge after `AgentService`, register cleanup before the SDK gateway resource is released, and add non-critical readiness state.
- [ ] Re-run lifespan tests and verify existing component states remain unchanged.

### Task 7: Documentation And Verification

**Files:**
- Modify: `README.md`
- Create: `tests/test_feishu_live.py` with `live` marker and environment-gated skip.

- [ ] Document secret rotation, long-connection setup, event subscription, permissions, environment variables, group @ behavior, plaintext reply limits, readiness, and restart command without embedding real credentials.
- [ ] Add a default-skipped live smoke that requires explicit rotated test credentials and target message/chat configuration.
- [ ] Run `python -m pytest -q`.
- [ ] Run `npm test`, `npm run build`, and existing Playwright tests to confirm the web client is unchanged.
- [ ] Confirm `queued=0/running=0`, restart with one Uvicorn worker, and verify all existing readiness components plus `feishu_bot`.

