# Agent Bug Diagnosis And Reliability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add evidence-backed middle-platform trace diagnosis and remove the live reliability failures that cause unsupported answers and rerank errors.

**Architecture:** A fixed Grafana client owns environment mappings and sanitization. Local Agent function tools expose only trace ID, environment and bounded time range; a Bug specialist combines sanitized log evidence with branch-scoped code RAG. AgentService applies a deterministic evidence gate to specialist answers.

**Tech Stack:** Python 3.11, OpenAI Agents SDK, FastAPI, httpx, Grafana/Loki, Chroma, pytest

---

### Task 1: Grafana configuration and client

**Files:**
- Modify: `knowledge/config/settings.py`
- Modify: `.env.example`
- Create: `knowledge/logs/__init__.py`
- Create: `knowledge/logs/grafana.py`
- Test: `tests/test_grafana_logs.py`

- [ ] Write failing tests for environment mapping, bounded time range, bearer injection, fixed LogQL, Grafana frame parsing, redaction and truncation.
- [ ] Run `python -m pytest tests/test_grafana_logs.py -q` and confirm failure before implementation.
- [ ] Implement `GrafanaLogClient.query_trace()` and serializable result models without logging credentials or raw responses.
- [ ] Add `GRAFANA_LOG_*` and middle environment mapping settings plus placeholder configuration.
- [ ] Run the focused tests and confirm they pass.

### Task 2: Log citation and Agent tools

**Files:**
- Modify: `knowledge/agent_runtime/context.py`
- Modify: `knowledge/api/schemas.py`
- Create: `knowledge/agent_runtime/bug_tools.py`
- Test: `tests/test_bug_tools.py`
- Modify: `tests/test_agent_run_context.py`
- Modify: `tests/test_agent_api.py`

- [ ] Write failing tests for `log_trace` citations, sanitized tool output and environment-to-branch code filters.
- [ ] Implement `create_trace_log_tool()` and `create_bug_code_search_tool()` with fixed server-owned scope.
- [ ] Extend the citation discriminator with `log_trace` while keeping raw log text out of public metadata.
- [ ] Run the focused context, API and tool tests.

### Task 3: Bug specialist topology and runtime wiring

**Files:**
- Modify: `knowledge/agent_runtime/agent_factory.py`
- Modify: `knowledge/api/app.py`
- Modify: `tests/test_agent_factory.py`
- Modify: `tests/test_app_lifespan.py`

- [ ] Write failing topology tests requiring `bug_diagnosis_expert` and its two fixed tools.
- [ ] Add Bug specialist instructions and Manager routing guidance.
- [ ] Build the Grafana client in FastAPI lifespan only when configuration is complete and expose readiness as `available`, `unavailable`, or `disabled`.
- [ ] Run topology and lifespan tests.

### Task 4: Deterministic evidence gate

**Files:**
- Modify: `knowledge/agent_runtime/service.py`
- Modify: `tests/test_agent_service.py`

- [ ] Write failing JSON and SSE tests where a specialist runs without citations and an unsupported final answer is produced.
- [ ] Replace unsupported specialist output with a fixed evidence-unavailable response.
- [ ] Buffer SSE final text only when a specialist has run and citations are still absent; emit the gated response instead of leaked unsupported text.
- [ ] Confirm greetings and evidenced answers retain existing behavior.

### Task 5: Rerank safety and obsolete-tool cleanup

**Files:**
- Modify: `knowledge/services/qwen_rerank_service.py`
- Modify: `knowledge/agent_runtime/agent_factory.py`
- Modify: `tests/test_qwen_rerank.py`
- Modify: `tests/test_agent_factory.py`

- [ ] Write a failing test proving every submitted rerank document remains below 48,000 characters and retains heading plus content.
- [ ] Implement bounded candidate construction with separate keyword and content budgets.
- [ ] Remove legacy approval/workflow tools while retaining the metric legacy document tool.
- [ ] Run focused rerank and topology tests.

### Task 6: API strictness and end-to-end verification

**Files:**
- Modify: `knowledge/api/schemas.py`
- Modify: `tests/test_agent_api.py`
- Modify: `tests/evals/agent_eval_cases.json`

- [ ] Configure `ChatRequest` to reject unknown fields and test unsupported `branch` returns 422.
- [ ] Add Bug eval cases for develop, test, prod, missing environment, missing logs and evidence-backed diagnosis.
- [ ] Run all backend tests with `python -m pytest -q`.
- [ ] Start the API and verify readiness, Grafana configuration status, greeting, evidence gating and branch-scoped Bug tools.
- [ ] Run an opt-in live Grafana smoke query when an explicit trace ID is available; otherwise verify authentication with a no-result query without printing credentials.
- [ ] Do not commit because the target directory is not a Git repository.
