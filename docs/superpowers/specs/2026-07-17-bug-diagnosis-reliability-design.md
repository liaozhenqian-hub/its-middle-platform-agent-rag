# Bug Diagnosis Reliability Design

## Goal

Make Bug diagnosis use the environment, trace ID, and request time already supplied by the user, and preserve the Graph's real terminal result instead of asking for duplicate information.

## Design

- Configure the middle-platform production Loki namespace as `api-center-master`.
- Deterministically extract `YYYY-MM-DD HH:mm:ss` request times from the original report. Treat times without an offset as Asia/Shanghai (`UTC+08:00`) and never accept a model-invented time.
- When request time is present, query a one-hour window centered on it. Without request time, retain the existing rolling 24-hour behavior.
- Store the Bug Graph's public answer in `AgentRunContext.response_override`. AgentService returns this value for JSON and SSE so `clarification_required`, `no_logs`, `unavailable`, and completed diagnoses are not rewritten by the Manager or generic evidence gate.
- Keep raw logs, credentials, prompts, and code bodies out of checkpoints, API responses, and audit logs.

## Error Handling

- Grafana timeout or transport failures return the Graph's explicit unavailable message.
- A successful zero-result query returns the Graph's no-logs message and the attempted time window citation.
- Only successful log evidence proceeds to code retrieval.

## Verification

- Unit tests cover deterministic time extraction, centered query windows, no-time fallback, and authoritative Graph answer propagation in JSON and SSE.
- A read-only smoke query verifies the known production trace against `api-center-master` without printing raw logs.

