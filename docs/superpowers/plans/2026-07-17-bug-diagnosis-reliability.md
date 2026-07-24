# Bug Diagnosis Reliability Implementation Plan

> **For agentic workers:** Execute inline with test-first checkpoints; this workspace is not a Git repository.

**Goal:** Correct production log lookup and prevent valid Bug reports from receiving duplicate clarification requests.

**Architecture:** Extend deterministic intake state with request time, center Loki queries on that time, and carry the Graph's public answer through Agent context as an authoritative override. Existing LangGraph topology and public chat response schemas remain unchanged.

**Tech Stack:** Python 3.11, Pydantic, LangGraph, OpenAI Agents SDK, httpx, pytest.

---

### Task 1: Request Time Intake

**Files:** `knowledge/bug_graph/intake.py`, `knowledge/bug_graph/models.py`, `tests/test_bug_graph_intake.py`

- [ ] Add a failing test for `请求时间 2026-07-16 11:48:34`.
- [ ] Parse and normalize the timestamp to an offset-aware ISO value.
- [ ] Verify the intake tests pass.

### Task 2: Targeted Grafana Query

**Files:** `knowledge/bug_graph/service.py`, `.env.example`, `.env`, `tests/test_bug_graph_service.py`

- [ ] Add a failing test asserting a centered 60-minute query window.
- [ ] Pass `now_ms` to the log client only from verified request time.
- [ ] Change the production namespace to `api-center-master`.
- [ ] Verify Graph and Grafana tests pass.

### Task 3: Authoritative Graph Results

**Files:** `knowledge/agent_runtime/context.py`, `knowledge/bug_graph/tool.py`, `knowledge/agent_runtime/service.py`, `tests/test_bug_graph_tool.py`, `tests/test_agent_service.py`

- [ ] Add failing JSON and SSE tests for preserving the Graph terminal answer.
- [ ] Store a public response override in run context.
- [ ] Prefer the override over Manager output and generic evidence fallback.
- [ ] Verify Agent service tests pass.

### Task 4: Regression And Restart

- [ ] Run all backend tests.
- [ ] Run a sanitized real-trace query against the corrected namespace.
- [ ] Confirm no queued or running sync jobs.
- [ ] Restart one Uvicorn worker and verify readiness.

