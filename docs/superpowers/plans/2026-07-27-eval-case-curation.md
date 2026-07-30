# Eval Case Curation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Safely reduce the active regression set to 64 purpose-specific cases and restore the complete 30-case Critical suite.

**Architecture:** Add one idempotent SQLite migration module containing the reviewed routing-smoke allowlist and action planner. The migration performs a dry-run by default, uses SQLite's online backup API before applying changes, exports an action manifest, restores the missing official case from the checked-in business dataset, and updates cases in one transaction.

**Tech Stack:** Python 3.11, sqlite3, pytest, JSON, existing quality case definitions.

---

### Task 1: Specify the curation policy

**Files:**
- Create: `knowledge/migrations/curate_eval_cases.py`
- Test: `tests/test_curate_eval_cases.py`

- [ ] **Step 1: Write failing policy tests**

Create fixture cases for all target suites and assert that planning produces 24 `routing-smoke`, archives other route cases, keeps multi-turn cases active, disables unqualified candidates, and deletes only rejected cases without result rows.

- [ ] **Step 2: Run the focused test and verify failure**

Run: `.venv-agent\Scripts\python.exe -m pytest tests/test_curate_eval_cases.py -q`

Expected: FAIL because `knowledge.migrations.curate_eval_cases` does not exist.

- [ ] **Step 3: Implement deterministic action planning**

Define `ROUTING_SMOKE_CASE_IDS`, `CurationAction`, and `plan_curation(connection)`. Validate that all allowlisted IDs exist and that exactly 24 are selected. Return actions without mutating the database.

- [ ] **Step 4: Run the focused test**

Run: `.venv-agent\Scripts\python.exe -m pytest tests/test_curate_eval_cases.py -q`

Expected: PASS.

### Task 2: Add backup, manifest, and transactional application

**Files:**
- Modify: `knowledge/migrations/curate_eval_cases.py`
- Modify: `tests/test_curate_eval_cases.py`

- [ ] **Step 1: Add failing safety tests**

Assert dry-run leaves the database byte-for-byte unchanged, apply creates a readable backup and manifest, repeated apply is idempotent, and a rejected case with an existing `eval_results` row is disabled rather than deleted.

- [ ] **Step 2: Run the safety tests and verify failure**

Run: `.venv-agent\Scripts\python.exe -m pytest tests/test_curate_eval_cases.py -q`

Expected: FAIL on missing apply behavior.

- [ ] **Step 3: Implement safe application**

Use `sqlite3.Connection.backup`, write the manifest after successful backup, execute updates/deletes inside `BEGIN IMMEDIATE`, and roll back on any exception. Require `--apply` for writes and expose `--database` plus `--output-dir` arguments.

- [ ] **Step 4: Run the safety tests**

Run: `.venv-agent\Scripts\python.exe -m pytest tests/test_curate_eval_cases.py -q`

Expected: PASS.

### Task 3: Restore and enrich the official Critical suite

**Files:**
- Modify: `knowledge/migrations/curate_eval_cases.py`
- Modify: `knowledge/migrations/enrich_critical_eval_cases.py`
- Modify: `tests/test_critical_cases.py`
- Modify: `tests/test_curate_eval_cases.py`

- [ ] **Step 1: Add a failing missing-case test**

Create a database without `write-delete-metric`, run curation, and assert the case is restored and receives `definition_for("write-delete-metric")` constraints.

- [ ] **Step 2: Verify the focused test fails**

Run: `.venv-agent\Scripts\python.exe -m pytest tests/test_critical_cases.py tests/test_curate_eval_cases.py -q`

Expected: FAIL because enrichment currently raises for missing official IDs.

- [ ] **Step 3: Implement canonical restoration**

Load the matching case from `storage/evaluations/real-business-regression-cases-60.json`, insert all schema fields, and then apply the official definition. Keep reserve cases optional so missing reserve data cannot block Critical repair.

- [ ] **Step 4: Run focused tests**

Run: `.venv-agent\Scripts\python.exe -m pytest tests/test_critical_cases.py tests/test_curate_eval_cases.py -q`

Expected: PASS.

### Task 4: Apply and verify production data

**Files:**
- Create at runtime: `storage/backups/agent_quality-<timestamp>.db`
- Create at runtime: `storage/evaluations/eval-case-curation-<timestamp>.json`

- [ ] **Step 1: Run dry-run**

Run: `.venv-agent\Scripts\python.exe -m knowledge.migrations.curate_eval_cases --dry-run`

Expected: reports 30 Critical, 10 conversation, 24 routing smoke, 70 archived, 3 disabled candidates, and 2 deletions.

- [ ] **Step 2: Apply the migration**

Run: `.venv-agent\Scripts\python.exe -m knowledge.migrations.curate_eval_cases --apply`

Expected: backup and manifest paths are printed, with 64 enabled and 137 total cases.

- [ ] **Step 3: Run verification**

Run: `.venv-agent\Scripts\python.exe -m pytest tests/test_critical_cases.py tests/test_curate_eval_cases.py -q`

Expected: PASS.

- [ ] **Step 4: Audit the live database**

Query counts grouped by `suite`, `enabled`, and `approval_state`; verify all 30 `OFFICIAL_CRITICAL_CASE_IDS` exist and have non-empty `required_facts_json`.
