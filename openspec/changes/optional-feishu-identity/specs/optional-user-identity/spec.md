## ADDED Requirements

### Requirement: Anonymous web identity
The system SHALL allow browser users to chat and access device-scoped personal memory without logging in. It SHALL issue a cryptographically random HttpOnly anonymous cookie, store only its SHA-256 hash, use an `anon:<uuid>` owner identifier, set SameSite=Strict, and slide the expiry up to 180 days while the device remains active.

#### Scenario: First anonymous request
- **WHEN** a browser calls a user-facing API without a bearer token, Feishu session, or valid anonymous cookie
- **THEN** the system creates an anonymous device identity, resolves it as the request owner, and returns its random cookie without exposing the persisted hash

#### Scenario: Returning anonymous device
- **WHEN** a browser presents a valid active anonymous cookie
- **THEN** the system resolves the same anonymous owner and extends the device and cookie expiry without changing the owner ID

### Requirement: Optional Feishu login
The system SHALL support optional Feishu OAuth login through the configured self-built application. When a tenant key is configured, authenticated identities SHALL be restricted to that tenant; when it is blank, every user authorized by the application SHALL be accepted. The callback URI SHALL be `http://172.18.26.1:8000/api/v1/auth/feishu/callback` unless explicitly reconfigured to another approved service address.

#### Scenario: Successful Feishu login
- **WHEN** a browser returns with a valid single-use OAuth state and authorization code for a user in the configured tenant
- **THEN** the system creates a server-side user session bound to that user's application-level `open_id`, persists only allowed profile fields, and redirects without persisting OAuth credentials or raw responses

#### Scenario: Invalid tenant or state
- **WHEN** the callback has an expired, reused, mismatched state or, when tenant restriction is enabled, identifies a user outside the configured tenant
- **THEN** the system rejects login without creating a user session or changing anonymous ownership

### Requirement: User session lifecycle
The system SHALL store only a hash of a random user session cookie, use a seven-day sliding expiry with a thirty-day absolute maximum, and keep user session and CSRF state independent from administrator authentication.

#### Scenario: Active session refresh
- **WHEN** an authenticated browser makes a request before both session deadlines
- **THEN** the system resolves the Feishu owner and extends only the sliding deadline up to the absolute deadline

#### Scenario: Logout
- **WHEN** an authenticated browser submits logout with the valid user CSRF token
- **THEN** the system revokes the user session, clears its cookie, and gives subsequent anonymous use a fresh device identity

### Requirement: Deterministic identity precedence
The system SHALL resolve identity in the order personal bearer token, Feishu user session, anonymous device cookie. It MUST NOT trust `X-Authenticated-User-ID` or any equivalent caller-supplied owner identifier.

#### Scenario: Bearer token overrides cookies
- **WHEN** a request contains a valid personal bearer token and any browser identity cookies
- **THEN** the system resolves the token's Feishu `open_id` and applies token scope restrictions

#### Scenario: Invalid bearer token
- **WHEN** a request supplies an invalid or revoked bearer token
- **THEN** the system returns 401 and does not fall back to a cookie identity

### Requirement: Conversation ownership
The system SHALL bind each web or Codex conversation to its resolved owner on first use and SHALL prevent a different owner from reading, continuing, or deleting that conversation. Feishu bot conversations SHALL be bound to the sender's `open_id`.

#### Scenario: Cross-owner conversation reuse
- **WHEN** a resolved owner supplies a conversation ID already bound to another owner
- **THEN** the system returns 404 without revealing the existing owner or conversation contents

### Requirement: Identity status API
The system SHALL expose the current identity kind, display data, authentication state, merge availability, CSRF token for Feishu sessions, and allowed capabilities without returning cookie values, session hashes, OAuth credentials, or personal token secrets.

#### Scenario: Anonymous identity status
- **WHEN** an anonymous browser calls `GET /api/v1/auth/me`
- **THEN** the response identifies “当前设备”, indicates that login is optional, and does not require authentication
