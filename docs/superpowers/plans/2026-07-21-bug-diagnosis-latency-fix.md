# Bug Diagnosis Latency Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate repeated environment clarification and unnecessary Manager/Intake model calls in the LangGraph Bug diagnosis flow.

**Architecture:** Route deterministic Bug requests and pending Bug conversations directly from `AgentService` to `BugDiagnosisGraphService`. Preserve the raw current user message at the tool boundary, merge newly supplied required fields over checkpoint state, and use deterministic parsing before any optional normalization.

**Tech Stack:** Python 3.11, OpenAI Agents SDK, LangGraph, AsyncSQLiteSaver, pytest/pytest-asyncio.

---

### Task 1: Protect raw user input

**Files:**
- Modify: `tests/test_bug_graph_tool.py`
- Modify: `knowledge/bug_graph/tool.py`

- [ ] Add a regression test where Manager's `bug_report` contains all environment options but `AgentRunContext.current_user_message` contains one explicit environment.
- [ ] Run the test and verify it fails because the generated argument is currently forwarded.
- [ ] Make the tool prefer `current_user_message` and keep the generated argument only as a compatibility fallback.
- [ ] Run the tool tests and verify they pass.

### Task 2: Merge clarification state deterministically

**Files:**
- Modify: `tests/test_bug_graph_service.py`
- Modify: `knowledge/bug_graph/intake.py`
- Modify: `knowledge/bug_graph/service.py`

- [ ] Add a checkpoint regression test whose first report mentions development, test, and production while the resumed message explicitly says development and supplies a trace ID.
- [ ] Add a parser test proving required-field clarification does not call the model normalizer.
- [ ] Run both tests and verify the expected failures.
- [ ] Add deterministic required-field parsing and merge the latest non-null values over checkpoint values.
- [ ] Keep the accumulated problem description for downstream diagnosis without re-parsing stale environment choices.
- [ ] Run Bug Graph tests and verify they pass.

### Task 3: Bypass Manager for Bug Graph requests

**Files:**
- Modify: `tests/test_agent_service.py`
- Modify: `knowledge/agent_runtime/service.py`
- Modify: `knowledge/api/app.py`

- [ ] Add JSON and SSE tests proving explicit Bug routing and pending clarification do not invoke the Agents SDK Runner.
- [ ] Run the tests and verify they fail because all requests currently enter Manager.
- [ ] Inject `BugDiagnosisGraphService` into `AgentService` and construct the public response/tool audit deterministically.
- [ ] Emit stable SSE lifecycle events for the direct path.
- [ ] Pass the service from FastAPI lifespan wiring.
- [ ] Run Agent/API tests and verify they pass.

### Task 4: Regression verification

**Files:**
- Verify: `tests/test_bug_graph_*.py`
- Verify: `tests/test_agent_service.py`
- Verify: complete `tests/` suite

- [ ] Run focused Bug Graph, Agent service, and API tests.
- [ ] Run the complete backend pytest suite.
- [ ] Confirm no source sync jobs are running before restarting the single-worker service.
- [ ] Restart only if the current service process is owned by this workspace and verify readiness.
