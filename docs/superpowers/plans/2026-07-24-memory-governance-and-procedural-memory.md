# Memory Governance And Procedural Memory Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Separate personal and domain memory governance, restrict 24-hour automatic confirmation to preferences and ordinary context, and add structured evidence-backed procedural memory that can safely guide Bug Graph.

**Architecture:** Keep `agent_memory.db` as the authoritative memory catalog and the existing memory Chroma collection as a recall index. Add explicit schema migrations and structured procedure/promotion tables, enforce ownership in repository/service/API layers, and integrate procedures with Bug Graph behind observe-only and enablement flags. Current code, Swagger, documents, and logs remain authoritative evidence.

**Tech Stack:** Python 3.11, FastAPI, aiosqlite, LangGraph, Chroma, Vue 3, Pinia, Element Plus, pytest, Vitest.

**Repository constraint:** The target directory is not a Git repository. Do not create commits, branches, worktrees, or pull requests while executing this plan.

---

## File Structure

### New Python modules

- `knowledge/memory/migrations.py`: ordered, idempotent memory schema migrations.
- `knowledge/memory/procedures.py`: structured procedure models, validation, matching, and conversion from fixed Bug Graph outcomes.
- `knowledge/memory/promotions.py`: domain-promotion validation, redaction, deduplication, and provenance.
- `knowledge/memory/conflicts.py`: current-evidence conflict recording and review-state transitions.

### Modified Python modules

- `knowledge/memory/models.py`: review states, procedure and promotion data types.
- `knowledge/memory/repository.py`: migrations, type-specific due queries, procedure CRUD, aggregate statistics, promotion lifecycle, and guarded updates.
- `knowledge/memory/service.py`: confirmation policy, user rejection, procedure recall, promotion, and conflict handling.
- `knowledge/memory/worker.py`: policy-driven maintenance and index-repair handling.
- `knowledge/memory/incidents.py`: create structured Bug procedures rather than text-only playbooks.
- `knowledge/memory/tools.py`: bounded procedure/entity recall output without internal identifiers.
- `knowledge/bug_graph/models.py`: selected procedure reference and observe-only route comparison fields.
- `knowledge/bug_graph/service.py`: procedure selection, observe-only comparison, controlled routing, and outcome reporting.
- `knowledge/api/app.py`: user/admin authorization, domain review APIs, promotion APIs, statistics, settings wiring, and readiness.
- `knowledge/config/settings.py`: procedure, promotion, retention, and conflict feature flags.
- `.env.example`: documented defaults.

### Modified Vue modules

- `web/src/types/api.ts`: procedure, promotion, review-state, statistics, and eligibility types.
- `web/src/stores/userMemory.ts`: owner reject action, grouped candidates, and countdown data.
- `web/src/stores/memory.ts`: domain-only administration, aggregate personal statistics, and promotion review.
- `web/src/views/MemoryView.vue`: separate automatically confirmable and explicit-review sections.
- `web/src/components/admin/MemoryPanel.vue`: domain review and redacted aggregate statistics only.

### Tests

- `tests/test_memory_migrations.py`
- `tests/test_memory_repository.py`
- `tests/test_memory_service.py`
- `tests/test_memory_worker.py`
- `tests/test_memory_api.py`
- `tests/test_procedural_memory.py`
- `tests/test_bug_incident_memory.py`
- `tests/test_bug_graph_procedural_memory.py`
- `tests/test_domain_memory_promotion.py`
- `tests/test_memory_conflicts.py`
- `web/src/stores/userMemory.test.ts`
- `web/src/stores/memory.test.ts`
- `web/src/views/MemoryView.test.ts`
- `web/src/components/admin/MemoryPanel.test.ts`

---

### Task 1: Introduce Explicit Memory Schema Migrations

**Files:**
- Create: `knowledge/memory/migrations.py`
- Modify: `knowledge/memory/models.py`
- Modify: `knowledge/memory/repository.py`
- Test: `tests/test_memory_migrations.py`

- [ ] **Step 1: Write failing migration tests**

Add tests proving a fresh database receives every migration once, an existing database created by the current `initialize()` path is upgraded without losing rows, and repeated initialization is idempotent.

```python
@pytest.mark.asyncio
async def test_memory_migrations_upgrade_existing_database_without_data_loss(tmp_path):
    path = tmp_path / "agent_memory.db"
    await create_legacy_memory_database(path, candidate_subject="answer_format")

    repository = MemoryRepository(path)
    await repository.initialize()
    await repository.initialize()

    async with aiosqlite.connect(path) as db:
        versions = await (await db.execute(
            "SELECT version FROM memory_schema_migrations ORDER BY version"
        )).fetchall()
        subject = await (await db.execute(
            "SELECT subject FROM memory_candidates"
        )).fetchone()
    assert versions == [(1,), (2,), (3,)]
    assert subject == ("answer_format",)
```

- [ ] **Step 2: Run the test and verify RED**

Run:

```powershell
.\.venv-agent\Scripts\python.exe -m pytest -q tests/test_memory_migrations.py
```

Expected: failure because `memory_schema_migrations` and the new tables do not exist.

- [ ] **Step 3: Add ordered migrations**

Define immutable migration records in `knowledge/memory/migrations.py` and apply them inside `MemoryRepository.initialize()` under `BEGIN IMMEDIATE`.

Migration 1 registers the current schema. Migration 2 adds:

```sql
CREATE TABLE memory_procedural_specs (
    record_id TEXT PRIMARY KEY,
    task_type TEXT NOT NULL,
    procedure_version INTEGER NOT NULL,
    trigger_conditions_json TEXT NOT NULL,
    required_inputs_json TEXT NOT NULL,
    environment_constraints_json TEXT NOT NULL,
    branch_constraints_json TEXT NOT NULL,
    steps_json TEXT NOT NULL,
    allowed_tools_json TEXT NOT NULL,
    minimum_evidence_grade TEXT NOT NULL,
    stop_conditions_json TEXT NOT NULL,
    fallback_actions_json TEXT NOT NULL,
    expected_output_json TEXT NOT NULL,
    validation_steps_json TEXT NOT NULL,
    success_count INTEGER NOT NULL DEFAULT 0,
    failure_count INTEGER NOT NULL DEFAULT 0,
    last_executed_at TEXT,
    reviewed_by TEXT,
    reviewed_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
```

Migration 3 adds `review_state`, `review_reason`, and `legacy_format` columns to memories/candidates plus `memory_domain_promotions` and `memory_conflicts` tables. Use safe `ALTER TABLE` detection through `PRAGMA table_info` rather than relying on `ALTER TABLE IF NOT EXISTS`.

- [ ] **Step 4: Add structured models**

Add frozen dataclasses for `ProceduralStep`, `ProceduralSpec`, `DomainPromotion`, and `MemoryConflict`. Limit procedure versions to positive integers and represent JSON arrays as tuples in Python.

- [ ] **Step 5: Run migration and repository regressions**

```powershell
.\.venv-agent\Scripts\python.exe -m pytest -q tests/test_memory_migrations.py tests/test_memory_repository.py
```

Expected: all tests pass and repeated initialization does not change row counts.

---

### Task 2: Restrict 24-Hour Automatic Confirmation By Memory Type

**Files:**
- Modify: `knowledge/memory/repository.py`
- Modify: `knowledge/memory/service.py`
- Modify: `knowledge/memory/worker.py`
- Test: `tests/test_memory_repository.py`
- Test: `tests/test_memory_service.py`
- Test: `tests/test_memory_worker.py`

- [ ] **Step 1: Write failing eligibility tests**

Create due user candidates for all five existing memory types. Assert only `user_preference` and `user_context` are returned and confirmed by maintenance.

```python
confirmed = await service.auto_confirm_due_candidates(now=now)
assert {item.memory_type for item in confirmed} == {
    "user_preference",
    "user_context",
}
assert (await repository.get_candidate(episode.id)).status == "candidate"
assert (await repository.get_candidate(procedure.id)).status == "candidate"
```

Also prove `decision_memory`, even when user-scoped, remains pending until explicitly confirmed.

- [ ] **Step 2: Run the focused tests and verify RED**

```powershell
.\.venv-agent\Scripts\python.exe -m pytest -q tests/test_memory_repository.py tests/test_memory_service.py tests/test_memory_worker.py
```

Expected: the current due query incorrectly selects every user-scoped type.

- [ ] **Step 3: Make eligibility explicit**

Change the repository API to:

```python
AUTO_CONFIRM_MEMORY_TYPES = ("user_preference", "user_context")

async def list_due_user_candidates(
    self,
    cutoff: datetime,
    *,
    memory_types: tuple[str, ...] = AUTO_CONFIRM_MEMORY_TYPES,
    limit: int = 100,
) -> list[MemoryCandidate]:
    ...
```

The SQL must require `scope_type='user'`, `status='candidate'`, `created_at<=cutoff`, and `memory_type IN (...)`.

- [ ] **Step 4: Preserve concurrency safety**

Keep status-guarded confirmation. If a user confirms or rejects while maintenance is running, maintenance skips the resolved candidate without failing the worker.

- [ ] **Step 5: Run focused memory tests**

Expected: preferences/context auto-confirm; decisions, incidents, and procedures stay pending.

---

### Task 3: Enforce Personal Ownership And Domain Administration

**Files:**
- Modify: `knowledge/memory/repository.py`
- Modify: `knowledge/memory/service.py`
- Modify: `knowledge/api/app.py`
- Test: `tests/test_memory_api.py`

- [ ] **Step 1: Write failing authorization tests**

Add API tests proving:

- an owner can list, confirm, reject, and delete their personal records;
- another user receives 404;
- administrator approval/rejection returns 404 for user-scoped candidates;
- administrator approval/rejection works for domain candidates;
- the default admin candidate list contains only domain candidates;
- the personal-statistics endpoint returns aggregate counts without owner IDs or summaries.

```python
response = client.post(
    f"/api/v1/admin/memory/candidates/{personal.id}/approve",
    headers=admin_csrf_headers,
)
assert response.status_code == 404

stats = client.get("/api/v1/admin/memory/personal-statistics")
assert stats.json() == {
    "candidate": {"user_preference": 1},
    "confirmed": {},
    "rejected": {},
    "deleted": {},
}
assert "owner_id" not in stats.text
```

- [ ] **Step 2: Run the tests and verify RED**

Expected: current administrator endpoints can mutate `scope_type='user'` records.

- [ ] **Step 3: Add an owner rejection endpoint**

Add:

```http
POST /api/v1/memory/candidates/{candidate_id}/reject
X-User-CSRF-Token: <session token>
```

Use the same ownership and CSRF checks as confirmation. The service actor is `user:{owner_id}`.

- [ ] **Step 4: Guard administrator review by scope**

Before administrator approve/reject, load the candidate and require `scope_type='domain'`. Return 404 for personal candidates so the endpoint does not reveal their existence.

Default `GET /api/v1/admin/memory/candidates` and `GET /api/v1/admin/memory` to `scope_type=domain`. Do not allow an ordinary query parameter to bypass this default and retrieve personal content.

- [ ] **Step 5: Add redacted personal statistics**

Add repository aggregation grouped by status and memory type. The response contains counts only. Do not return `owner_id`, subject, summary, citations, source turn, or timestamps precise enough to identify one user.

- [ ] **Step 6: Harden emergency deletion**

Keep a separate administrator delete endpoint requiring `{ "confirm": true, "reason": "sensitive_content|policy_violation|user_request" }`. Append an audit event and never expose the deleted content in the audit details.

- [ ] **Step 7: Run API and authentication regressions**

```powershell
.\.venv-agent\Scripts\python.exe -m pytest -q tests/test_memory_api.py tests/test_user_auth_api.py
```

---

### Task 4: Separate User And Administrator Memory Interfaces

**Files:**
- Modify: `web/src/types/api.ts`
- Modify: `web/src/stores/userMemory.ts`
- Modify: `web/src/stores/memory.ts`
- Modify: `web/src/views/MemoryView.vue`
- Modify: `web/src/components/admin/MemoryPanel.vue`
- Test: `web/src/stores/userMemory.test.ts`
- Test: `web/src/stores/memory.test.ts`
- Create: `web/src/views/MemoryView.test.ts`
- Create: `web/src/components/admin/MemoryPanel.test.ts`

- [ ] **Step 1: Write failing Store and component tests**

Prove the user UI separates:

- preferences/context with a 24-hour countdown;
- decisions, incidents, and procedures requiring explicit confirmation;
- confirmed memories with delete controls.

Prove the admin panel renders domain candidates and aggregate personal statistics but never a personal owner ID, personal summary, or personal approve/reject action.

- [ ] **Step 2: Run Vitest and verify RED**

```powershell
cd web
npm test -- --run src/stores/userMemory.test.ts src/stores/memory.test.ts src/views/MemoryView.test.ts src/components/admin/MemoryPanel.test.ts
```

- [ ] **Step 3: Extend API types**

Add:

```typescript
export type MemoryReviewState = "pending" | "approved" | "rejected" | "review_required";

export interface PersonalMemoryStatistics {
  candidate: Record<MemoryType, number>;
  confirmed: Record<MemoryType, number>;
  rejected: Record<MemoryType, number>;
  deleted: Record<MemoryType, number>;
}
```

Expose `auto_confirm_eligible` and `auto_confirm_at` on user candidate responses. Compute these server-side; do not duplicate policy rules in Vue.

- [ ] **Step 4: Add user reject behavior**

Implement `userMemory.reject(id)` with user CSRF. Remove the candidate locally after success and show an explicit confirmation dialog in `MemoryView.vue`.

- [ ] **Step 5: Redesign the user page sections**

Use unframed full-width sections rather than nested cards. Display type, domain, human-readable source name, confidence, and either countdown or “需要你确认”. Never display internal owner, candidate, chunk, source, or turn IDs.

- [ ] **Step 6: Redesign the admin panel**

Rename it “领域记忆审核”. Add domain/type/review-state filters, evidence state, validity, promotion source, and review actions. Add a compact aggregate statistics band for personal memory. Keep emergency deletion behind a confirmation dialog with reason selection.

- [ ] **Step 7: Run focused Vue tests and production type checking**

```powershell
cd web
npm test -- --run src/stores/userMemory.test.ts src/stores/memory.test.ts src/views/MemoryView.test.ts src/components/admin/MemoryPanel.test.ts
npm run build
```

---

### Task 5: Add Structured Bug Procedural Memory

**Files:**
- Create: `knowledge/memory/procedures.py`
- Modify: `knowledge/memory/models.py`
- Modify: `knowledge/memory/repository.py`
- Modify: `knowledge/memory/service.py`
- Modify: `knowledge/memory/incidents.py`
- Test: `tests/test_procedural_memory.py`
- Test: `tests/test_bug_incident_memory.py`

- [ ] **Step 1: Write failing structured-validation tests**

Cover ordered steps, task type, environment/branch constraints, allowed tools, minimum evidence grade, stop conditions, fallback actions, output contract, validation steps, version, and evidence references.

Reject specs containing credentials, raw logs, trace IDs, raw LogQL, arbitrary URLs, code正文, prompts, or chain-of-thought markers.

```python
with pytest.raises(ValueError, match="unsafe procedural memory"):
    validator.validate(replace(valid_spec, steps=(
        ProceduralStep(capability="log_query", purpose="Authorization: Bearer abc", ...),
    )))
```

- [ ] **Step 2: Run tests and verify RED**

Expected: structured procedure APIs do not exist.

- [ ] **Step 3: Implement fixed procedural structures**

Define `ProceduralStep` and `ProceduralSpec` with tuple fields. Store only capability names such as `query_trace_logs`, `extract_log_signals`, `search_branch_code`, `inspect_contract_and_docs`, and `validate_fix`. Do not persist tool-call arguments.

- [ ] **Step 4: Add repository lifecycle methods**

Implement:

```python
async def upsert_procedural_spec(self, record_id: str, spec: ProceduralSpec) -> None: ...
async def get_procedural_spec(self, record_id: str) -> ProceduralSpec | None: ...
async def list_matching_procedures(self, *, owner_id: str | None, domain_id: str | None,
                                   task_type: str, environment: str, branch: str,
                                   limit: int) -> list[tuple[Memory, ProceduralSpec]]: ...
async def record_procedure_outcome(self, record_id: str, *, success: bool) -> None: ...
```

Only confirmed, unexpired, non-`review_required` records match.

- [ ] **Step 5: Convert Bug recorder output**

Keep the existing evidence gate. Build the first structured procedure deterministically from the fixed Bug Graph stages. The model may summarize a user-facing label, but it cannot select tools, URLs, branches, evidence thresholds, or stop conditions.

The current text-only procedure remains `legacy-v1`. New structured candidates use `procedure_version=2` and require explicit user confirmation.

- [ ] **Step 6: Keep Chroma content bounded**

Index a short searchable summary containing task, domain, triggers, and step purposes. Store the complete structured spec only in SQLite.

- [ ] **Step 7: Run procedural and incident tests**

```powershell
.\.venv-agent\Scripts\python.exe -m pytest -q tests/test_procedural_memory.py tests/test_bug_incident_memory.py
```

---

### Task 6: Integrate Procedures With Bug Graph In Observe-Only Mode

**Files:**
- Modify: `knowledge/config/settings.py`
- Modify: `.env.example`
- Modify: `knowledge/memory/service.py`
- Modify: `knowledge/bug_graph/models.py`
- Modify: `knowledge/bug_graph/service.py`
- Modify: `knowledge/api/app.py`
- Test: `tests/test_bug_graph_procedural_memory.py`
- Test: `tests/test_memory_settings.py`

- [ ] **Step 1: Write failing selection and fallback tests**

Prove matching uses server-controlled user, domain, task, environment, and branch. Prove a `prod/master` procedure cannot match `test/develop`. Prove absence, expiry, conflict, malformed data, or retrieval failure falls back to the existing graph with no user-visible failure.

- [ ] **Step 2: Add feature settings**

```python
memory_procedural_guidance_enabled: bool = Field(
    default=False, alias="MEMORY_PROCEDURAL_GUIDANCE_ENABLED"
)
memory_procedural_observe_only: bool = Field(
    default=True, alias="MEMORY_PROCEDURAL_OBSERVE_ONLY"
)
memory_procedural_recall_limit: int = Field(
    default=3, ge=1, le=10, alias="MEMORY_PROCEDURAL_RECALL_LIMIT"
)
```

Document the same defaults in `.env.example`.

- [ ] **Step 3: Add a controlled selection stage**

After required fields are validated and before log query, select at most one procedure. Add only IDs, version, matched capabilities, and comparison state to the checkpoint. Do not store procedure正文 in LangGraph state.

- [ ] **Step 4: Implement observe-only comparison**

When observe-only is true, execute the existing fixed route and calculate whether the selected procedure would have changed stage order, stop decisions, or evidence requirements. Store only a quality span and reason codes.

- [ ] **Step 5: Enable controlled guidance path**

When guidance is enabled and observe-only is false, a procedure may choose among the existing whitelisted graph stages and stop conditions. It cannot add nodes, call tools directly, alter environment/branch, reduce the minimum evidence grade, or bypass current evidence collection.

- [ ] **Step 6: Record outcomes**

Increment success only when the run completes with accepted evidence and a diagnosis. Increment failure for procedure-selected runs that terminate with internal errors or explicit evidence contradiction. Clarification and no-log outcomes are neutral.

- [ ] **Step 7: Run Bug Graph regressions**

```powershell
.\.venv-agent\Scripts\python.exe -m pytest -q tests/test_bug_graph_procedural_memory.py tests/test_bug_graph_service.py tests/test_bug_graph_streaming.py tests/test_memory_settings.py
```

---

### Task 7: Add Domain Promotion And Review

**Files:**
- Create: `knowledge/memory/promotions.py`
- Modify: `knowledge/memory/repository.py`
- Modify: `knowledge/memory/service.py`
- Modify: `knowledge/api/app.py`
- Modify: `web/src/types/api.ts`
- Modify: `web/src/stores/memory.ts`
- Modify: `web/src/components/admin/MemoryPanel.vue`
- Test: `tests/test_domain_memory_promotion.py`
- Test: `web/src/stores/memory.test.ts`
- Test: `web/src/components/admin/MemoryPanel.test.ts`

- [ ] **Step 1: Write failing promotion tests**

Cover eligibility, citation existence, domain requirement, sensitive-content rejection, deduplication, provenance, administrator review, default 90-day validity, and no mutation of the source personal memory.

- [ ] **Step 2: Add promotion settings**

```env
MEMORY_DOMAIN_PROMOTION_ENABLED=false
MEMORY_DOMAIN_DEFAULT_RETENTION_DAYS=90
```

- [ ] **Step 3: Implement promotion validation**

Only confirmed `episodic_memory` or structured `procedural_memory` with sufficient evidence may be promoted. Resolve every evidence reference against active catalog/Chroma data. Redact personal identifiers and incident-specific trace/log content before candidate creation.

- [ ] **Step 4: Add APIs**

```http
POST /api/v1/admin/memory/promotions
GET  /api/v1/admin/memory/promotions?state=pending
POST /api/v1/admin/memory/promotions/{id}/approve
POST /api/v1/admin/memory/promotions/{id}/reject
```

Creation accepts `source_memory_id`, `target_domain_id`, `public_summary`, and `valid_until`. Approval creates or confirms a distinct `scope_type='domain'` record and preserves provenance.

- [ ] **Step 5: Add review UI**

Show source type, target domain, evidence state, redacted summary, duplicate warning, and validity. Require secondary confirmation on approval. Do not show the personal owner ID.

- [ ] **Step 6: Run domain promotion tests**

Expected: no personal record is automatically promoted and no domain record activates without administrator approval.

---

### Task 8: Add Conflict Detection, Review State, And Repair

**Files:**
- Create: `knowledge/memory/conflicts.py`
- Modify: `knowledge/memory/repository.py`
- Modify: `knowledge/memory/service.py`
- Modify: `knowledge/memory/worker.py`
- Modify: `knowledge/bug_graph/service.py`
- Test: `tests/test_memory_conflicts.py`

- [ ] **Step 1: Write failing conflict tests**

Prove current evidence can override a procedure, one conflict is audited, repeated conflicts mark `review_required`, and review-required records disappear from recall without being deleted.

- [ ] **Step 2: Implement normalized conflict reasons**

Use fixed codes: `branch_mismatch`, `environment_mismatch`, `missing_evidence`, `source_deleted`, `contract_changed`, and `runtime_contradiction`. Store no raw evidence正文.

- [ ] **Step 3: Add threshold setting**

```env
MEMORY_CONFLICT_REVIEW_THRESHOLD=2
```

The service marks `review_required` after two unresolved conflicts. A user may delete personal memory; an administrator may re-review domain memory.

- [ ] **Step 4: Add index repair**

If SQLite confirmation succeeds but Chroma upsert fails, enqueue a bounded repair record. The memory remains non-recallable through the vector path until repair succeeds, while SQLite remains authoritative.

- [ ] **Step 5: Run conflict and worker tests**

```powershell
.\.venv-agent\Scripts\python.exe -m pytest -q tests/test_memory_conflicts.py tests/test_memory_worker.py
```

---

### Task 9: Migration Command, Rollout Gates, And Full Verification

**Files:**
- Create: `knowledge/cli/migrate_memory_governance.py`
- Modify: `README.md`
- Modify: `.env.example`
- Test: `tests/test_memory_governance_migration.py`

- [ ] **Step 1: Write failing dry-run and idempotency tests**

The command must report counts for auto-confirm-eligible candidates, explicit-review candidates, legacy procedures, confirmed personal records, and domain records. Dry-run must not mutate the database.

- [ ] **Step 2: Implement the migration command**

```powershell
.\.venv-agent\Scripts\python.exe -m knowledge.cli.migrate_memory_governance --dry-run
.\.venv-agent\Scripts\python.exe -m knowledge.cli.migrate_memory_governance --apply
```

On apply:

- mark existing user episodic/decision/procedural candidates as explicit review;
- mark text-only procedures `legacy-v1`;
- leave confirmed ownership unchanged;
- do not create domain records;
- append aggregate audit events only.

- [ ] **Step 3: Run focused migration tests**

Expected: dry-run has zero writes and repeated apply produces the same counts.

- [ ] **Step 4: Run all automated verification**

```powershell
.\.venv-agent\Scripts\python.exe -m pytest -q
cd web
npm test -- --run
npm run build
```

- [ ] **Step 5: Back up persistent memory before apply**

Create a timestamped copy of `storage/agent_memory.db` and its WAL/SHM companions after stopping the process or using SQLite online backup. Verify the backup opens and returns the same memory/candidate counts.

- [ ] **Step 6: Apply in phases**

1. Deploy schema, policy, ownership, and UI with procedural guidance disabled.
2. Verify personal candidate actions and domain admin review.
3. Enable structured candidate generation only.
4. Enable procedural observe-only mode and collect comparison metrics.
5. Require the critical regression suite to pass before enabling guidance.
6. Enable domain promotion after personal procedure behavior is stable.

- [ ] **Step 7: Enforce release gates**

- Cross-user personal memory exposure: 0.
- Administrator personal approve/reject capability: 0.
- Automatic confirmation of incident/procedure/decision: 0.
- Domain memory without administrator review: 0.
- Sensitive persistence findings: 0.
- Bug Graph fixed-route regression pass rate: 100%.
- Observe-only procedure route agreement: at least 95% before guidance enablement.
- Procedure-guided diagnosis cannot reduce evidence grade below current policy.

- [ ] **Step 8: Restart safely**

Confirm source sync, evaluation, and memory extraction queues have no running jobs. Restart exactly one Uvicorn worker. Verify `/health/ready`, `/chat`, user memory, admin domain memory, Bug clarification, Bug diagnosis, and procedure fallback.

---

## Public Interface Changes

New endpoints:

```http
POST /api/v1/memory/candidates/{id}/reject
GET  /api/v1/admin/memory/personal-statistics
POST /api/v1/admin/memory/emergency-delete/{id}
POST /api/v1/admin/memory/promotions
GET  /api/v1/admin/memory/promotions
POST /api/v1/admin/memory/promotions/{id}/approve
POST /api/v1/admin/memory/promotions/{id}/reject
```

Changed behavior:

- Administrator candidate and confirmed-memory lists are domain-only.
- Administrator approve/reject returns 404 for personal records.
- User candidate responses expose `auto_confirm_eligible` and `auto_confirm_at`.
- Personal preferences/context auto-confirm after 24 hours.
- Personal decisions, Bug episodes, and procedures require explicit confirmation.

New environment variables:

```env
MEMORY_PROCEDURAL_GUIDANCE_ENABLED=false
MEMORY_PROCEDURAL_OBSERVE_ONLY=true
MEMORY_PROCEDURAL_RECALL_LIMIT=3
MEMORY_DOMAIN_PROMOTION_ENABLED=false
MEMORY_DOMAIN_DEFAULT_RETENTION_DAYS=90
MEMORY_CONFLICT_REVIEW_THRESHOLD=2
```

## Explicit Non-Goals

- No chain-of-thought persistence or display.
- No raw tool trajectory replay.
- No write-capable procedure tools.
- No automatic personal-to-domain promotion.
- No replacement of current RAG, code, Swagger, or log evidence.
- No Redis, Celery, PostgreSQL, Neo4j, or additional vector database.
