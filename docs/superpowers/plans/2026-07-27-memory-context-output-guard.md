# Memory Context Output Guard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep conversation and long-term memory available to the Agent while preventing internal memory context from appearing in any public answer channel.

**Architecture:** Strengthen the memory prompt contract and add a shared output-layer guard used by both complete answers and streamed deltas. The stream guard buffers only the undecided prefix, suppresses a detected leak, and emits one safe fallback at flush.

**Tech Stack:** Python, pytest, existing `MemoryService`, `sanitize_public_answer`, and `PublicAnswerStream`.

---

### Task 1: Reproduce public memory-context leakage

**Files:**
- Modify: `tests/test_public_answer.py`

- [ ] Add tests for full output, Markdown-wrapped output, direct memory-type labels, split streamed headers, and unaffected normal streaming.
- [ ] Run `.venv-agent\Scripts\python.exe -m pytest tests/test_public_answer.py -q` and confirm the new tests fail because internal context remains visible.

### Task 2: Implement the shared output guard

**Files:**
- Modify: `knowledge/agent_runtime/public_answer.py`

- [ ] Add internal-memory marker detection and a public fallback constant.
- [ ] Truncate non-stream answers before a leaked block or return the fallback when no safe prefix exists.
- [ ] Extend `PublicAnswerStream` with prefix-safe buffering and leak suppression across delta boundaries.
- [ ] Run the focused public-answer tests and confirm they pass.

### Task 3: Strengthen the memory prompt contract

**Files:**
- Modify: `knowledge/memory/service.py`
- Modify: `tests/test_memory_summary_runtime.py`

- [ ] Add a failing assertion that augmented input marks memory as internal and prohibits reproduction.
- [ ] Update both summary and confirmed-memory wrappers with the explicit prohibition.
- [ ] Run memory and Agent service tests.

### Task 4: Verify all channels

**Files:**
- Test: `tests/test_public_answer.py`
- Test: `tests/test_agent_service.py`
- Test: `tests/test_memory_summary_runtime.py`

- [ ] Run the focused backend tests.
- [ ] Run the complete backend test suite.
- [ ] Run frontend tests and production build to ensure SSE consumption remains compatible.
