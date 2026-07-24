## ADDED Requirements

### Requirement: Feishu-bound personal token creation
The system SHALL allow only a Feishu-authenticated browser session with valid user CSRF to create a named personal token bound to that user's `open_id`. The token SHALL have only `agent:query` and/or `memory:read` scopes.

#### Scenario: Create personal Codex token
- **WHEN** a Feishu-authenticated user submits a valid unique token name and allowed scopes
- **THEN** the system returns the plaintext token exactly once and persists only its SHA-256 hash, non-secret display prefix, metadata, and scopes

#### Scenario: Anonymous token creation attempt
- **WHEN** an anonymous device or personal token calls a token management endpoint
- **THEN** the system returns 401 or 403 and creates no token

### Requirement: Long-lived revocable token lifecycle
Personal tokens SHALL have no automatic expiry, SHALL record creation and last-use timestamps, and SHALL become unusable immediately when revoked. Listing tokens MUST NOT return plaintext or hashes.

#### Scenario: Token use updates metadata
- **WHEN** Codex authenticates successfully with an active personal token
- **THEN** the system resolves the bound Feishu owner and updates `last_used_at` without exposing the token in logs or quality data

#### Scenario: Revoke token
- **WHEN** the owning Feishu-authenticated user revokes a token with valid user CSRF
- **THEN** subsequent requests using that token return 401 immediately

### Requirement: Scoped machine access
The system SHALL require `agent:query` for Agent chat and `memory:read` for reading personal memory. Personal tokens MUST NOT authorize administrator APIs, business writes, memory deletion, identity merge, logout, or token management.

#### Scenario: Read-only Codex access
- **WHEN** Codex supplies an active token with both allowed scopes and `X-Client-Channel: codex`
- **THEN** it can query the Agent and read memory as the bound Feishu `open_id` while all excluded operations remain forbidden

#### Scenario: Missing scope
- **WHEN** a valid personal token calls an endpoint without the required scope
- **THEN** the system returns 403 without executing the operation

### Requirement: Sensitive token redaction
The system MUST NOT write personal token plaintext to source files, environment files, logs, Agent prompts, quality storage, OpenSpec artifacts, or audit event details.

#### Scenario: Authentication failure audit
- **WHEN** bearer authentication fails
- **THEN** logs and audit records contain only a generic failure reason and request metadata that cannot reconstruct the token
