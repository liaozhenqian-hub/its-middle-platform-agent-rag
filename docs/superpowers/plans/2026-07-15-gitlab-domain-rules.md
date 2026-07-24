# GitLab Domain Rules Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Accurately classify both middle-platform GitLab repositories with editable multi-pattern presets and let Git webhooks work without a reversible credential master key.

**Architecture:** Add a dedicated SQLite table for one-way webhook hashes and keep a legacy encrypted-store read fallback. Move repository-specific glob presets into a pure TypeScript utility, flatten normalized pattern arrays into the existing API contract, and render those arrays as editable rows in the Vue dialog.

**Tech Stack:** Python 3.11, FastAPI, aiosqlite, pytest, Vue 3, TypeScript, Pinia, Element Plus, Vitest.

---

### Task 1: Persist Webhook Hashes Without Encryption

**Files:**
- Modify: `knowledge/catalog/migrations.py`
- Modify: `knowledge/catalog/repository.py`
- Test: `tests/test_catalog_repository.py`

- [ ] **Step 1: Write failing repository tests**

Add a test that initializes the catalog, creates a Git source, calls `set_webhook_secret_hash(source_id, digest)`, verifies `get_webhook_secret_hash(source_id)`, overwrites the digest, and verifies source deletion cascades to the hash row.

- [ ] **Step 2: Run the focused test and verify failure**

Run: `.venv-agent\Scripts\python.exe -m pytest tests/test_catalog_repository.py -q`

Expected: failure because the migration and repository methods do not exist.

- [ ] **Step 3: Add migration v6 and repository methods**

Create `source_webhook_secrets(source_id PRIMARY KEY, secret_hash, updated_at)` with a cascading foreign key. Implement:

```python
async def set_webhook_secret_hash(self, source_id: str, secret_hash: str) -> None:
    ...

async def get_webhook_secret_hash(self, source_id: str) -> str | None:
    ...
```

Use `INSERT ... ON CONFLICT(source_id) DO UPDATE` and include the new table in soft-delete content purging.

- [ ] **Step 4: Run focused repository tests**

Run: `.venv-agent\Scripts\python.exe -m pytest tests/test_catalog_repository.py -q`

Expected: all tests pass.

### Task 2: Decouple Git Source Creation From the Master Key

**Files:**
- Modify: `knowledge/api/app.py`
- Modify: `tests/test_source_management_api.py`

- [ ] **Step 1: Change the API test to omit `CatalogSecretStore`**

The Git source test must construct `create_app(...)` without `catalog_secret_store`, create the source, verify the hash through `CatalogRepository`, and successfully validate a webhook.

- [ ] **Step 2: Run the focused API test and verify failure**

Run: `.venv-agent\Scripts\python.exe -m pytest tests/test_source_management_api.py::test_admin_creates_git_source_and_webhook_is_validated_and_deduplicated -q`

Expected: HTTP 503 `secret storage unavailable`.

- [ ] **Step 3: Store and validate the one-way hash through the catalog**

Remove the secret-store requirement from Git source creation, call `catalog.set_webhook_secret_hash(...)`, and make the webhook endpoint read the catalog hash first. If it is absent and a `CatalogSecretStore` exists, read the legacy encrypted `webhook_secret_hash` so existing sources remain valid.

- [ ] **Step 4: Verify current and legacy webhook behavior**

Add a legacy fallback test using `_secret_store(repository)`, then run:

`.venv-agent\Scripts\python.exe -m pytest tests/test_source_management_api.py -q`

Expected: Git without a master key and legacy encrypted hashes both pass; Swagger encryption tests remain unchanged.

### Task 3: Add Repository-Specific Multi-Pattern Presets

**Files:**
- Modify: `web/src/utils/sources.ts`
- Modify: `web/src/utils/sources.test.ts`

- [ ] **Step 1: Write failing preset and payload tests**

Cover `erp/loctek-middle-platform`, `erp/loctek-middle-platform-web`, and an unknown project. Verify representative paths are present, blank/duplicate patterns are removed, rules are flattened, and priorities are deterministic.

- [ ] **Step 2: Run Vitest and verify failure**

Run: `npm test -- --run src/utils/sources.test.ts`

Working directory: `web`

Expected: failure because `gitRulePreset` and array patterns do not exist.

- [ ] **Step 3: Implement typed presets and payload normalization**

Export:

```typescript
export type GitDomainId = "metric-platform" | "approval-flow" | "workflow";
export type GitDomainPatterns = Record<GitDomainId, string[]>;
export function gitRulePreset(projectPath: string): GitDomainPatterns;
```

Update `createGitPayload` to accept `GitDomainPatterns`, trim and de-duplicate each list, and emit one API rule per pattern with ascending priorities.

- [ ] **Step 4: Run focused utility tests**

Run: `npm test -- --run src/utils/sources.test.ts`

Working directory: `web`

Expected: all utility tests pass.

### Task 4: Render Editable Rule Lists in the Admin Dialog

**Files:**
- Modify: `web/src/components/admin/AddSourceDialog.vue`

- [ ] **Step 1: Replace scalar rule state with arrays**

Initialize three empty arrays and apply `gitRulePreset(project.path_with_namespace)` whenever a project is selected. Keep branch discovery unchanged.

- [ ] **Step 2: Add list editing controls and validation**

For each domain, render every pattern as an input with an icon-only delete button and tooltip, plus an `添加规则` button. Require at least one nonblank rule in each domain before submitting.

- [ ] **Step 3: Run frontend tests and production build**

Run: `npm test -- --run`

Run: `npm run build`

Working directory: `web`

Expected: all tests pass and Vite emits `dist` without TypeScript errors.

### Task 5: Full Regression Verification

**Files:**
- Verify only; no planned source edits.

- [ ] **Step 1: Run the complete backend suite**

Run: `.venv-agent\Scripts\python.exe -m pytest -q`

Expected: all backend tests pass.

- [ ] **Step 2: Run final frontend suite and build**

Run: `npm test -- --run`

Run: `npm run build`

Working directory: `web`

Expected: all frontend tests pass and production build succeeds.

- [ ] **Step 3: Inspect changed files and credential safety**

Confirm no token value, Authorization header, source body, or generated storage artifact was added to source files. Because the directory is not a Git repository, report the changed file list directly instead of committing.
