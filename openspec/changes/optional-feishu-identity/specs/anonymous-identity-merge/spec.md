## ADDED Requirements

### Requirement: Explicit merge preview
The system SHALL NOT move anonymous data during OAuth callback. After login it SHALL provide a preview that counts mergeable conversations, confirmed memories, pending candidates, exact duplicates, and conflicts for the anonymous device associated with the login state.

#### Scenario: Login with anonymous history
- **WHEN** a user signs in from an active anonymous device that owns conversations or memory data
- **THEN** the user session reports merge availability and the merge preview describes the planned effects without mutating either identity

### Requirement: Confirmed and idempotent merge
The system SHALL merge anonymous data only after an authenticated user explicitly confirms with a valid user CSRF token. A repeated request for the same source and target SHALL return the original completed result and MUST NOT duplicate data.

#### Scenario: User confirms merge
- **WHEN** the Feishu-authenticated user confirms the current anonymous merge preview
- **THEN** the system moves eligible conversation ownership, summaries, extraction jobs, candidates, and memories to the user's `open_id`, completes a durable merge job, disables the anonymous device, and clears its cookie

#### Scenario: Merge fails partway
- **WHEN** a merge step fails before completion
- **THEN** the system records a retryable failed job, leaves the anonymous device active, and permits an idempotent retry without data loss

### Requirement: Duplicate and conflict handling
The system SHALL deduplicate memories with equal normalized facts in the same scope and SHALL preserve existing Feishu confirmed memory when anonymous memory conflicts on the same scope, domain, type, and subject. A conflicting anonymous fact SHALL become a pending candidate for administrator review.

#### Scenario: Exact duplicate memory
- **WHEN** anonymous and Feishu owners have the same normalized memory fact in the same scope
- **THEN** the merge retains one confirmed Feishu-owned memory and records the anonymous row as deduplicated

#### Scenario: Conflicting memory
- **WHEN** an anonymous confirmed memory conflicts with an existing Feishu confirmed memory for the same semantic slot
- **THEN** the Feishu memory remains active and the anonymous memory is converted into a Feishu-owned pending candidate

### Requirement: Declined merge isolation
The system SHALL allow a user to decline or ignore merge without deleting anonymous data. Logged-in chat SHALL start a new Feishu-owned conversation and MUST NOT continue an unmerged anonymous conversation.

#### Scenario: User declines merge
- **WHEN** a logged-in user declines the preview
- **THEN** anonymous data remains associated with the anonymous device and new logged-in activity uses the Feishu owner in a new conversation

