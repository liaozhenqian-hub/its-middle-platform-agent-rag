## ADDED Requirements

### Requirement: Namespaced vector storage
The system SHALL store knowledge and personal-memory vectors in `vector_entries` using `(collection_name, id)` as the stable key and SHALL preserve original content, metadata, content hash and 1024-dimensional embedding.

#### Scenario: Same ID in different collections
- **WHEN** two entries use the same ID in different collections
- **THEN** both entries coexist and collection-scoped reads return only the requested entry

#### Scenario: Invalid embedding dimension
- **WHEN** an upsert supplies an embedding whose dimension is not 1024
- **THEN** the operation fails before writing any item from that batch

### Requirement: Chroma-compatible repository contract
The PostgreSQL vector provider SHALL implement upsert, metadata update, delete, cosine search, chunk reads, ID reads, keyword metadata pagination and count with the same externally observed semantics as the current repository.

#### Scenario: Cosine search
- **WHEN** a query embedding and metadata filter are supplied
- **THEN** results are ordered by ascending cosine distance and every result satisfies the filter

#### Scenario: Idempotent upsert
- **WHEN** the same collection and ID are written again
- **THEN** the stored content, metadata and embedding are replaced without increasing the collection count

### Requirement: Personal memory isolation
Personal-memory vector reads SHALL require collection, owner, scope and space filters, with optional domain filtering, and SHALL never return another owner's memory.

#### Scenario: Cross-owner nearest vector
- **WHEN** another owner's memory is the mathematically closest vector
- **THEN** it is excluded before results are returned

### Requirement: Non-authoritative shadow retrieval
The system SHALL support pgvector shadow retrieval while Chroma remains authoritative and SHALL record only non-sensitive result IDs, latency and overlap.

#### Scenario: Shadow disagreement
- **WHEN** pgvector results differ from Chroma results
- **THEN** the user receives the Chroma-derived answer and the difference is available only as a sanitized quality metric
