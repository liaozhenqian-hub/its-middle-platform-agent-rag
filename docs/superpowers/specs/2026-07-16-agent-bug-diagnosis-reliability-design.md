# Agent Bug Diagnosis And Reliability Design

## Goal

Extend the internal middle-platform question-answering system so it can use a
trace ID to query Grafana/Loki, correlate sanitized failures with the correct
code branch, and produce evidence-backed Bug analysis. At the same time,
remove the reliability failures already observed in live Agent evaluations.

## Supported Scope

The log tool only supports the `middle` system.

| User environment | Loki environment | Code branch |
| --- | --- | --- |
| develop | develop | develop |
| test | test | develop |
| prod | prod | master |

The model cannot supply an arbitrary Grafana URL, datasource UID, namespace,
LogQL expression, Git source ID, or branch. Server-side configuration owns all
of those values.

## Log Query Tool

Add a fixed function tool:

```text
query_middle_trace_logs(trace_id, environment, time_range_minutes=60)
```

Inputs are validated as follows:

- `trace_id`: 6-200 characters using letters, digits, dots, colons, dashes and
  underscores;
- `environment`: `develop`, `test`, or `prod`;
- `time_range_minutes`: 1-60.

The client posts a range query to the configured Grafana datasource endpoint.
The LogQL expression is built by code from the fixed namespace and escaped
trace ID. Bearer credentials are read only from environment configuration.

The parser returns at most 20 relevant entries and extracts timestamps, log
levels, service/logger names, exception types, messages and Java stack frames.
Authorization values, cookies, API keys, tokens, passwords, email addresses,
phone numbers and large request/response bodies are removed or masked. A
single entry is capped at 2,000 characters and total model-facing log content
is capped at 30,000 characters.

## Bug Diagnosis Agent

Add `bug_diagnosis_expert` as a Manager tool. It receives the original Bug
report and must:

1. identify or ask for the environment;
2. query logs when a trace ID is present;
3. extract exception class, application class, method and line evidence;
4. search code using the configured environment-to-branch mapping;
5. use Swagger and knowledge documents when they provide contract evidence;
6. distinguish confirmed facts, likely causes and unknowns;
7. return repair and verification steps.

The response format is: problem summary, log evidence, code location, likely
cause with confidence, alternative causes, repair options, verification steps,
and missing information.

## Citations And Privacy

Add a `log_trace` citation type. Public metadata may contain only environment,
trace ID, query time range, log count, exception types and truncation status.
Raw log lines are never returned in API citations or tool audit records.

The Grafana URL, bearer token, datasource UIDs and namespaces never enter
Agent context, SQLite sessions, remote tracing, log messages or API responses.

## Reliability Changes

- Cap each Qwen rerank candidate below the provider's 50,000-character limit,
  preserving headings before keywords and content.
- Remove legacy approval/workflow knowledge tools whose metadata scope always
  returns zero records.
- Add an evidence gate: when an internal specialist was invoked but produced
  no citations, replace unsupported factual output with a deterministic
  evidence-unavailable response.
- Add branch-scoped code search for the Bug expert. Develop and test use the
  develop Git source; production uses the master Git source.
- Reject unknown chat request fields instead of silently ignoring unsupported
  scope such as `branch`.

Identical MCP tool-call suppression is not included in this implementation
because the Agents SDK executes MCP tools outside the local function-tool
wrapper. It will be addressed by a later deterministic metric workflow rather
than a partial prompt-only workaround.

## Testing

Tests cover configuration, environment mappings, query construction, auth
header injection, HTTP failures, Loki response parsing, redaction, output
limits, log citations, Bug Agent topology, branch filters, rerank truncation,
evidence gating and unknown API fields.

Live Grafana tests are opt-in and require an explicit trace ID. The normal test
suite uses a fake HTTP transport and must not use or print real credentials.

## Acceptance Criteria

- A valid middle-platform trace query returns sanitized log evidence.
- Develop and test Bug analysis searches develop code; production searches
  master code.
- A Bug answer with evidence includes both `log_trace` and code citations when
  matching code exists.
- No evidence produces a refusal rather than an invented diagnosis.
- Rerank no longer sends candidates above the provider limit.
- Existing RAG, Agent, API, source-sync and frontend tests continue to pass.
