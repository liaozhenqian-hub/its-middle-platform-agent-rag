## ADDED Requirements

### Requirement: Idempotent resumable migration
The system SHALL migrate SQLite and Chroma data in deterministic batches with stable run IDs, persisted progress and idempotent replay.

#### Scenario: Interrupted batch
- **WHEN** a migration process stops after committing a batch
- **THEN** rerunning the same migration resumes after the committed progress and does not duplicate prior rows

### Requirement: Secret-safe migration
The migration SHALL copy encrypted credentials as ciphertext and SHALL not persist or emit plaintext credentials, document bodies, log bodies, prompts or embeddings in reports or intermediate files.

#### Scenario: Migration error
- **WHEN** a row or vector cannot be migrated
- **THEN** the report identifies only its stable ID, source scope and normalized error type

### Requirement: Relationship consistency gate
The system SHALL verify table counts, primary-key sets, foreign keys, status distributions and sampled content hashes before PostgreSQL is selected.

#### Scenario: Verification mismatch
- **WHEN** any required relationship verification differs from the source snapshot
- **THEN** provider configuration remains unchanged and the cutover is rejected

### Requirement: Vector quality gate
The system SHALL verify collection counts, ID sets, metadata/hash samples, Top-10 overlap and latency before pgvector becomes authoritative.

#### Scenario: Shadow below threshold
- **WHEN** Top-10 overlap is below 90 percent or pgvector P90 latency exceeds the Chroma baseline by more than 20 percent
- **THEN** Chroma remains authoritative and pgvector cutover is rejected

### Requirement: Reversible staged rollout
The system SHALL switch relationship and vector providers independently, retain SQLite and Chroma read-only for at least one release, and document deterministic rollback steps.

#### Scenario: Relationship cutover failure
- **WHEN** the PostgreSQL smoke or Critical gate fails during the approved window
- **THEN** the service is stopped, configured back to SQLite plus Chroma and restarted without deleting PostgreSQL data

#### Scenario: Vector cutover failure
- **WHEN** pgvector retrieval fails after selection
- **THEN** the vector provider can be returned to Chroma without changing the relational provider
