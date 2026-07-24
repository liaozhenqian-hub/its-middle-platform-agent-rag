# Feishu Bot Bridge Design

## Goal

Connect the existing middle-platform Agent to a Feishu application bot through
Feishu's long-connection event mode. Users send a text message in Feishu, the
bridge invokes the existing `AgentService`, and the answer is sent back to the
same Feishu message context.

The separately approved Manager business-scope guardrails are implemented in
the same change.

## Connection And Runtime

- Use `lark-oapi==1.7.1` and its WebSocket long-connection client.
- No public webhook endpoint is added.
- FastAPI lifespan creates `FeishuBotBridge` after `AgentService` is ready.
- The SDK's blocking WebSocket loop runs in one background thread. Event
  callbacks submit Agent coroutines to the FastAPI event loop with
  `asyncio.run_coroutine_threadsafe`.
- Shutdown stops the bridge and joins the thread without blocking indefinitely.
- Feishu startup failure degrades the service: web chat and all existing tools
  remain available, while readiness reports `feishu_bot=unavailable`.

## Credentials And Configuration

Credentials are read only from environment variables:

- `FEISHU_BOT_ENABLED=false`
- `FEISHU_APP_ID`
- `FEISHU_APP_SECRET`
- `FEISHU_EVENT_DB=storage/feishu_bot.db`
- `FEISHU_REPLY_MAX_CHARS=3500`
- `FEISHU_GROUP_REQUIRE_MENTION=true`
- `FEISHU_AGENT_TIMEOUT_SECONDS=180`

The App Secret is never written to source code, SQLite, Agent context, traces,
tool audits, or logs. Configuration and health output reveal only whether the
bot is enabled and connected. Credentials already exposed in chat must be
rotated before enabling the integration.

## Incoming Message Policy

- Accept only `im.message.receive_v1` text events.
- Ignore messages sent by bots/apps and ignore the bridge's own responses.
- Private-chat text is processed directly.
- Group-chat text is processed only when it contains an @ mention when
  `FEISHU_GROUP_REQUIRE_MENTION=true`.
- Mention placeholders are removed before sending text to the Agent.
- Empty text and unsupported message types are ignored without invoking the
  Agent.
- Use Feishu `event_id` as a persistent idempotency key so SDK reconnects and
  event retries cannot generate duplicate Agent runs or replies.

## Conversation And Scope Mapping

- Feishu conversation ID is deterministically `feishu:<chat_id>`.
- A group therefore has one shared Agent history, matching the visible group
  conversation. Existing AgentService locks serialize overlapping messages.
- Private chats also use their Feishu chat ID and remain isolated.
- Feishu requests use the default `middle-platform` knowledge space with no
  fixed domain, allowing Manager routing across the three specialists.
- The bridge never accepts client-controlled model names, source IDs, branches,
  Grafana scopes, Swagger URLs, or MCP tools.

## Replies

- Reply to the originating Feishu message using the bot identity.
- First version sends plain text. Markdown control characters are preserved as
  text rather than interpreted as an interactive card.
- Append a compact citation list containing source type, title, and public
  source ID. Do not append excerpts, raw logs, tool arguments, or MCP output.
- Split long output on paragraph boundaries into chunks no longer than
  `FEISHU_REPLY_MAX_CHARS` and send them sequentially.
- Agent timeout or failure produces one short capability-unavailable reply;
  exception messages and credentials are not returned to Feishu.

## Persistent Event State

Use a dedicated SQLite database with WAL mode and a small migration-managed
table:

- `event_id` primary key;
- `message_id`, `chat_id`, and status (`processing`, `completed`, `failed`);
- attempt count and created/updated timestamps;
- sanitized failure type only.

Claiming an event is atomic. Completed and processing events are not run again.
A failed event may be reclaimed once after a service restart. Message text,
Agent answers, credentials, citations, and tool output are never stored in this
database.

## Manager Business Scope Guardrails

Extend `MANAGER_INSTRUCTIONS` according to the approved guardrail design:

- support middle-platform knowledge, integration, requirement analysis, Bug
  diagnosis, code/document/Swagger lookup, and read-only metric queries;
- answer a greeting in one short sentence and redirect to business work;
- do not continue casual or unrelated general-knowledge conversations;
- ask one clarification question when a request might be business-related;
- forbid unsupported writes, permission bypass, sensitive output, and internal
  factual claims without evidence.

No specialist prompt, metric MCP ordering, LangGraph flow, or evidence gate is
changed.

## Tests

- Settings defaults, path resolution, credential completeness, reply limit,
  and timeout validation.
- Event parser: private text, group mention removal, group without mention,
  bot sender, unsupported type, malformed content, and empty text.
- SQLite idempotency across restart, atomic duplicate claim, one failed retry,
  and no message/answer/credential persistence.
- Bridge: event-to-Agent mapping, fixed knowledge scope, timeout, sanitized
  failure reply, citation summary, answer splitting, duplicate suppression, and
  thread shutdown.
- Lifespan readiness for disabled, available, and degraded Feishu states.
- Manager prompt boundary assertions plus all existing Agent tests.
- No live Feishu call in the default suite. An opt-in smoke test runs only when
  explicit rotated test credentials and a test chat/message are supplied.

## Operational Prerequisites

Before enabling:

1. Rotate the exposed App Secret and place the new value only in `.env`.
2. In Feishu Open Platform select long-connection event delivery.
3. Subscribe to `im.message.receive_v1`.
4. Grant and publish the required receive-message and send-as-bot permissions.
5. Add the bot to the intended test group.
6. Restart the single Uvicorn worker and verify `feishu_bot` readiness.

