# Chroma Metadata Pagination Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the BM25 metadata index from paginated Chroma reads so the current 38,000-record knowledge base does not exceed SQLite's variable limit.

**Architecture:** Keep pagination inside `VectorStoreRepository`, preserving the existing retrieval and Agent interfaces. Read metadata in fixed-size pages, preserve the caller's filter on every page, and deduplicate records by Chroma record ID before returning them to BM25.

**Tech Stack:** Python 3.11, Chroma, pytest

---

### Task 1: Add repository pagination behavior

**Files:**
- Modify: `tests/test_vector_store_repository.py`
- Modify: `knowledge/repositories/vector_store_repository.py:183-208`

- [ ] **Step 1: Write the failing pagination test**

Add a fake Chroma collection that returns pages for offsets `0`, `2`, and `4`, with one duplicate ID across pages. Set `repo._metadata_page_size = 2`, call `get_keyword_index_records(where={"app_id": "middle-platform"})`, and assert:

```python
assert collection.calls == [
    {"include": ["metadatas"], "where": {"app_id": "middle-platform"}, "limit": 2, "offset": 0},
    {"include": ["metadatas"], "where": {"app_id": "middle-platform"}, "limit": 2, "offset": 2},
    {"include": ["metadatas"], "where": {"app_id": "middle-platform"}, "limit": 2, "offset": 4},
]
assert [record.chunk_id for record in records] == ["a", "b", "c"]
```

- [ ] **Step 2: Run the test and verify the current one-shot read fails**

Run:

```powershell
.\.venv-agent\Scripts\python.exe -m pytest tests\test_vector_store_repository.py -k paginates_keyword_metadata -q
```

Expected: FAIL because the repository does not send `limit` and `offset` and does not request subsequent pages.

- [ ] **Step 3: Implement the minimal pagination loop**

Add an internal default page size and replace the one-shot read with:

```python
_METADATA_PAGE_SIZE = 2000

page_size = getattr(self, "_metadata_page_size", _METADATA_PAGE_SIZE)
offset = 0
seen_record_ids: set[str] = set()
index_records: list[KeywordIndexRecord] = []
while True:
    records = self.vector_store._collection.get(
        **get_kwargs,
        limit=page_size,
        offset=offset,
    )
    record_ids = records.get("ids") or []
    for record_id, raw_metadata in zip(
        record_ids,
        records.get("metadatas") or [],
    ):
        normalized_id = str(record_id)
        if normalized_id in seen_record_ids:
            continue
        seen_record_ids.add(normalized_id)
        metadata = dict(raw_metadata or {})
        index_records.append(
            KeywordIndexRecord(
                chunk_id=str(metadata.get("chunk_id") or normalized_id),
                heading=str(metadata.get("heading", "")),
                keywords=str(metadata.get("bm25_keywords", "")),
                metadata=metadata,
            )
        )
    if len(record_ids) < page_size:
        break
    offset += len(record_ids)
return index_records
```

- [ ] **Step 4: Run repository tests**

Run:

```powershell
.\.venv-agent\Scripts\python.exe -m pytest tests\test_vector_store_repository.py -q
```

Expected: all repository tests pass.

### Task 2: Verify the real knowledge base

**Files:**
- No production file changes

- [ ] **Step 1: Run all backend tests**

Run:

```powershell
.\.venv-agent\Scripts\python.exe -m pytest -q
```

Expected: 236 pass and the credential-gated live smoke test skips.

- [ ] **Step 2: Build the real BM25 pipeline**

Instantiate `RetrievalPipelineRegistry(Settings())`, request the `middle-platform` pipeline, and confirm it indexes the current collection without `too many SQL variables`.

- [ ] **Step 3: Run real scoped retrieval checks**

Search approval-flow and workflow code scopes and assert both return at least one final result with a code citation candidate. Rerank is allowed to fall back to RRF because its separate 50,000-character limit is outside this change.

- [ ] **Step 4: Record the non-Git constraint**

Do not run `git add` or `git commit`; the project directory is not a Git repository.
