# Chroma Metadata Pagination Design

## Context

The knowledge collection currently contains about 38,000 records. Building the
in-memory BM25 index calls `VectorStoreRepository.get_keyword_index_records()`,
which requests every matching Chroma metadata row in one call. Chroma then
fails with `too many SQL variables`, so code and document RAG tools return no
evidence and specialist agents retry until they exhaust their turn budget.

The product direction is an internal integration-support question-answering
system. It should help other departments understand APIs and code, assess
requirement feasibility, and locate likely causes of reported bugs. Reliable
retrieval is therefore a prerequisite for later agent-quality work.

## Scope

This change only makes Chroma keyword-index metadata reads paginated.

It does not change:

- retrieval result schemas;
- BM25 scoring or tokenization;
- vector search behavior;
- Agent prompts or orchestration;
- branch selection;
- rerank input limits;
- source synchronization behavior.

Those concerns remain separate follow-up work.

## Design

`VectorStoreRepository.get_keyword_index_records(where)` will repeatedly call
the Chroma collection with:

- `include=["metadatas"]`;
- the original normalized `where` filter, when present;
- `limit=2000`;
- increasing `offset` values starting at zero.

Pagination stops when Chroma returns fewer than 2,000 IDs. Records are merged
in page order and deduplicated by record ID. The first occurrence wins. This
protects BM25 construction from duplicate records if collection contents move
during a read, without changing the public return type.

The page size is an internal repository constant for the first version. It is
well below the current Chroma maximum batch size and SQLite variable limit. A
new environment setting is unnecessary until there is evidence that operators
need to tune it.

## Failure Behavior

Any Chroma page failure aborts the read and preserves the existing exception
behavior. The system must not silently construct a partial BM25 index because
partial retrieval would be harder to detect than a failed tool call.

An empty result produces an empty list. Missing metadata entries are converted
to empty dictionaries using the existing normalization behavior.

## Verification

Tests will cover:

- multiple pages with offsets `0`, `2`, and `4` using a small test page size;
- propagation of the same `where` filter to every page;
- stable record ordering;
- duplicate ID removal across pages;
- empty collections;
- page failures remaining visible.

After unit tests pass, verification will include:

- all 237 backend tests;
- construction of the real BM25 pipeline from the current collection;
- confirmation that `too many SQL variables` no longer occurs;
- real approval-flow and workflow questions producing code citations.

Rerank may still fall back to RRF for oversized candidates. That known issue is
outside this pagination change and will be reported separately rather than
mixed into the fix.
