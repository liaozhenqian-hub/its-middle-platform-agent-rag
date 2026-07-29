## ADDED Requirements

### Requirement: Configurable relational provider
The system SHALL select SQLite or PostgreSQL through server-owned configuration while preserving existing public API behavior. PostgreSQL configuration SHALL prefer `DATABASE_URL`, otherwise safely compose a URL from split PG variables, and SHALL never expose credentials through logs, readiness or validation errors.

#### Scenario: Existing SQLite deployment
- **WHEN** `DATA_STORE_PROVIDER=sqlite`
- **THEN** the system uses the existing SQLite repositories without requiring PostgreSQL configuration

#### Scenario: PostgreSQL deployment
- **WHEN** `DATA_STORE_PROVIDER=postgres` and valid PostgreSQL configuration is present
- **THEN** the system initializes the shared connection resource and injects PostgreSQL repository implementations

### Requirement: Transactional PostgreSQL schema
The system SHALL manage all application-owned PostgreSQL tables through Alembic and SHALL use TIMESTAMPTZ, JSONB, BOOLEAN, foreign keys and indexes appropriate to PostgreSQL.

#### Scenario: Empty Schema provisioning
- **WHEN** Alembic upgrade runs against an empty approved Schema
- **THEN** it creates every application table and index exactly once and a repeated upgrade makes no additional structural change

#### Scenario: Transaction failure
- **WHEN** an operation fails before its transaction commits
- **THEN** none of that operation's partial writes remain visible

### Requirement: Atomic worker claims
The system SHALL use an atomic PostgreSQL transaction with `FOR UPDATE SKIP LOCKED` or an equivalent single-statement claim for queued work.

#### Scenario: Competing workers
- **WHEN** two workers attempt to claim the same eligible job concurrently
- **THEN** at most one worker receives that job and the other worker may claim a different unlocked job

### Requirement: PostgreSQL Agent and Graph state
The system SHALL provide a PostgreSQL Session implementation compatible with Agents SDK semantics and SHALL use the official PostgreSQL LangGraph Checkpointer for Bug diagnosis state.

#### Scenario: Service restart
- **WHEN** the service restarts after saving conversation messages or a pending Bug interrupt
- **THEN** the same owner and conversation can resume the history or Bug diagnosis without exposing checkpoint payloads publicly

#### Scenario: Expired Bug interrupt
- **WHEN** a pending Bug diagnosis is older than 24 hours
- **THEN** the system discards the expired resume state and starts a new diagnosis flow

### Requirement: Provider-aware readiness
The system SHALL report the selected relational provider and its availability without leaking connection details, while retaining legacy SQLite/Chroma readiness fields for one compatibility release.

#### Scenario: Selected PostgreSQL is unavailable
- **WHEN** PostgreSQL is the selected provider and its readiness query fails
- **THEN** readiness reports the database component unavailable and does not claim the application is ready
