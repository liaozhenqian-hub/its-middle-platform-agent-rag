# Memory Governance And Procedural Memory Design

## 1. Goal

Reorganize the current memory system into clearly owned layers, prevent administrators from managing ordinary personal memories, and turn the existing Bug diagnosis procedure text into a structured, evidence-backed procedural memory that can safely guide future Bug Graph runs.

The design keeps product documents, code, Swagger, and current logs as the source of truth. Memory supplies context and reusable process, but never overrides current evidence.

## 2. Current State

The project currently contains the following persistence layers:

| Layer | Storage | Current purpose |
|---|---|---|
| Agent session | `storage/agent_sessions.db` | Raw conversation context for one conversation |
| Conversation summary | `storage/agent_memory.db` | Bounded summary for one user and conversation |
| Typed long-term memory | SQLite plus the memory Chroma collection | Preferences, context, events, decisions, and procedures |
| Entity memory | Entity, alias, relation, and evidence tables | User-scoped service/API/code relationships created by Bug diagnosis |
| Bug Graph checkpoint | `storage/bug_graph.db` | Interrupt and resume state for a diagnosis, retained for 24 hours |
| Quality history | `storage/agent_quality.db` | Questions, answers, feedback, routing, and timing for evaluation |
| Knowledge catalog | Catalog SQLite plus the main Chroma collection | Authoritative code, documents, and Swagger knowledge |

The current typed memory scopes support `user`, `conversation`, `team`, `domain`, and `global`, but production behavior mainly uses `user` and `domain`. The administration page currently combines all candidates and permits an administrator to approve or reject user-scoped candidates. This is technically isolated by `owner_id`, but it violates the intended ownership model.

The current procedural memory is a bounded text summary created by successful Bug diagnoses. It does not yet represent preconditions, ordered steps, failure branches, tool constraints, validation, or versioning as structured fields.

## 3. Memory Taxonomy

### 3.1 Working Memory

Working memory consists of the active Agent SDK session and the active LangGraph state. It is conversation-bound and short-lived. It may contain current user input and graph control fields, but it must not be treated as a confirmed long-term fact.

### 3.2 Conversation Summary Memory

Conversation summary memory is scoped by `conversation_id`, `user_id`, space, and domain. It contains bounded fields:

- current goals;
- confirmed facts from the conversation;
- unresolved items;
- explicit preferences;
- a short recent-turn summary.

It is used only when continuing the same owned conversation. It is context, not a citation and not authoritative enterprise knowledge.

### 3.3 Personal Semantic Memory

Personal semantic memory contains:

- `user_preference`;
- `user_context`;
- user-specific `decision_memory` where the decision is explicitly made by that user.

These records are visible and manageable by the owner. Candidates may be automatically confirmed after 24 hours because their effect is limited to the same user. The owner can confirm early, reject before confirmation, and delete a confirmed record at any time.

### 3.4 Episodic Bug Memory

`episodic_memory` records a resolved or strongly correlated Bug incident. It stores only bounded, sanitized facts such as environment, branch, service, endpoint, exception identifiers, evidence grade, and public evidence references.

It is created only when Bug Graph completes with `correlated` or `contract_supported` evidence and at least one allowed code, product-document, or Swagger citation. It requires explicit owner confirmation and is excluded from 24-hour automatic confirmation.

### 3.5 Procedural Memory

`procedural_memory` represents a reusable Agent execution playbook. The initial supported task type is `bug_diagnosis`. It stores an abstract method, not chain-of-thought and not a replay of raw tool arguments.

A procedural record contains:

- task type and domain;
- trigger conditions;
- required inputs;
- environment and branch constraints;
- ordered steps;
- allowed tool capabilities;
- minimum evidence grade;
- deterministic stop and fallback conditions;
- expected output sections;
- validation steps;
- evidence references;
- procedure version and validity period;
- successful-use and failed-use counters;
- scope, owner, status, and review metadata.

Personal procedural candidates require explicit owner confirmation. They never use the 24-hour automatic confirmation path.

### 3.6 Entity Memory

Entity memory stores canonical entities, aliases, typed relations, environment and branch constraints, confidence, and evidence references. Initial entities include services, API paths, controllers, methods, documents, and Swagger operations.

Entity memory supplies retrieval hints. It cannot prove current implementation or deployment without current code, Swagger, or log evidence.

### 3.7 Domain Memory

Domain memory is shared inside exactly one domain: approval flow, workflow, or metric platform. It may contain reviewed decisions, episodic patterns, procedures, and entity relations.

Domain memory is never automatically confirmed. It enters service through an explicit promotion and administrator review workflow. The administrator must be able to edit the proposed public summary and validity period without editing the original personal record.

## 4. Ownership And Authorization

### 4.1 Personal Memory

- Only the owner may view full candidate and confirmed content.
- Only the owner may confirm, reject, or delete personal memory.
- Anonymous browser identities remain isolated by their server-issued device identity.
- Feishu users are isolated by the resolved Feishu owner identity.
- Administrators may view aggregate and redacted safety metadata, not full ordinary personal content.
- Administrators retain an audited emergency-delete operation for sensitive or prohibited content. Emergency deletion must not double as approval or rejection.

### 4.2 Domain Memory

- Domain candidates are visible to administrators in the review queue.
- Only administrators may approve, reject, expire, or delete domain memory.
- Approval preserves the target domain and never creates global scope implicitly.
- Domain memory requires evidence references, a reviewer identity, review timestamp, and validity period.

### 4.3 Automatic Confirmation Matrix

| Memory type | User scope | Domain scope |
|---|---|---|
| User preference | Auto-confirm after 24 hours | Not allowed |
| User context | Auto-confirm after 24 hours | Not allowed |
| User decision | Explicit owner confirmation | Manual review |
| Bug episode | Explicit owner confirmation | Manual review |
| Procedure | Explicit owner confirmation | Manual review |
| Entity relation | Evidence-gated activation for the owner | Manual promotion and review |

The maintenance worker must select due candidates by both scope and eligible memory type. It must not auto-confirm all `scope_type='user'` candidates.

## 5. Structured Procedural Memory Model

Procedural memory uses a dedicated structured table linked to the existing memory record rather than encoding the complete playbook in `normalized_fact`.

Required fields:

```text
memory_id
task_type
procedure_version
trigger_conditions_json
required_inputs_json
environment_constraints_json
branch_constraints_json
steps_json
allowed_tools_json
minimum_evidence_grade
stop_conditions_json
fallback_actions_json
expected_output_json
validation_steps_json
success_count
failure_count
last_executed_at
reviewed_by
reviewed_at
```

Each ordered step contains an operation capability, purpose, required inputs, produced signal types, and next-step condition. It must not contain live trace IDs, credentials, unrestricted URLs, raw LogQL, raw log bodies, complete code, prompts, or chain-of-thought.

## 6. Candidate Creation And Promotion

### 6.1 Personal Candidate Creation

Completed question-and-answer turns may generate personal preference, context, and explicitly attributable decision candidates through the existing extraction worker.

Bug Graph may generate episodic and procedural candidates only after evidence gating. The procedure generator derives a normalized playbook from graph node outcomes and fixed server-side capabilities; it does not ask the model to invent arbitrary tools or steps.

### 6.2 Domain Promotion

A confirmed personal Bug episode or procedure may be proposed for domain promotion when all conditions hold:

- evidence grade is `correlated` or `contract_supported`;
- all evidence references still resolve to active catalog records;
- a domain can be determined;
- the record contains no personal identifiers or incident-specific sensitive text;
- it is not already represented by an equivalent active domain record.

Promotion creates a new domain candidate with provenance back to the personal source but without exposing its owner in public retrieval. It does not mutate the personal record.

The first release supports two promotion sources:

- an administrator manually promotes a high-quality historical answer or personal candidate;
- Bug Graph proposes an evidence-backed procedure for administrator review.

Ordinary answers cannot automatically become active domain memory.

## 7. Recall And Runtime Integration

Memory selection occurs before evidence collection with a fixed budget:

1. Load the owned conversation summary for same-conversation continuity.
2. Recall matching confirmed personal preferences and context.
3. For Bug tasks, recall matching personal and domain procedures by task, domain, environment, and branch.
4. Recall matching entity relations as retrieval hints.
5. Execute current log, code, document, and Swagger evidence collection.
6. Validate that recalled memory is consistent with current evidence.
7. Generate the answer from current evidence; mention memory only as historical context where useful.

A recalled procedure can determine the graph route and stop conditions but cannot supply a current root cause. Missing current logs or code evidence still produces the existing calibrated partial or clarification response.

When current authoritative evidence contradicts memory, runtime ignores the memory and records a conflict event. Repeated conflicts mark the memory `review_required` and remove it from recall.

## 8. Administration And User Experience

### 8.1 User Memory Page

The user page has separate sections for:

- automatically confirmable preferences and context, displaying the remaining review window;
- Bug incidents requiring explicit confirmation;
- procedural playbooks requiring explicit confirmation;
- confirmed memories with forget controls.

Users can inspect why a candidate was created using public source names, not internal chunk or source identifiers.

### 8.2 Administrator Memory Page

The administrator page defaults to domain candidates and confirmed domain memories. It includes:

- domain, memory type, evidence grade, source type, validity, and review state;
- approve, edit public summary, reject, expire, and delete actions;
- domain-promotion candidates;
- aggregate personal-memory metrics by type and status;
- a separately authorized and audited emergency-delete flow.

The page does not expose ordinary personal memory contents or provide approve/reject actions for `scope_type='user'`.

## 9. Retention And Versioning

- Personal preference and context retain the existing default retention unless deleted or superseded.
- Personal Bug episodes and procedures use explicit validity and may be superseded by a newer version.
- Domain procedures default to 90 days and require re-review after expiry.
- A Git branch or source-version change does not automatically invalidate a procedure, but unresolved evidence references or repeated conflicts set `review_required`.
- Deleting a parent memory disables its structured procedure and removes its memory index entry.
- Conversation summaries are deleted with the owned conversation.
- Bug Graph checkpoints retain the existing 24-hour interrupt TTL and are not promoted into long-term memory.

## 10. Observability And Audit

Audit events record candidate creation, owner confirmation or rejection, automatic confirmation, promotion request, administrator review, conflict, expiration, and deletion. Events store identifiers, type, status, and reason codes, not memory正文 or model reasoning.

Metrics include candidate counts, owner confirmation rate, automatic confirmation rate, deletion rate, domain promotion rate, procedure match rate, successful use rate, conflict rate, and expired-memory rate. Personal metrics are aggregated and do not expose owner identifiers in the administration UI.

## 11. Migration

Existing records are migrated conservatively:

- `user_preference` and `user_context` candidates keep their current timestamps and remain eligible for the 24-hour rule.
- Existing user-scoped `episodic_memory`, `decision_memory`, and `procedural_memory` candidates are removed from automatic confirmation eligibility.
- Existing procedural text remains readable but is marked `legacy-v1` and is not used to control Bug Graph until converted to the structured schema and explicitly confirmed.
- Existing confirmed personal records remain owned and retrievable by the same owner.
- No personal record is automatically promoted to a domain.

The migration is idempotent and reports counts before applying changes.

## 12. Failure Handling

- Extraction or maintenance failure does not block chat.
- Procedure recall failure falls back to the fixed Bug Graph route.
- Invalid structured JSON rejects the candidate rather than partially applying it.
- Missing evidence prevents domain approval.
- Concurrent owner and administrator actions use status-guarded updates.
- Memory index failure leaves the SQLite status authoritative and queues an index repair.

## 13. Test Strategy

Tests cover:

- personal ownership across anonymous browser, Feishu, bot, and API identities;
- administrator inability to approve or reject personal candidates;
- 24-hour automatic confirmation for preferences and context only;
- explicit confirmation for episodes and procedures;
- domain promotion provenance, deduplication, redaction, and review;
- structured procedure validation, versioning, expiry, and deletion cascade;
- Bug Graph procedure matching and fixed fallback behavior;
- branch and environment isolation;
- entity-memory evidence validation;
- current-evidence contradiction and `review_required` behavior;
- no raw logs, credentials, code正文, prompts, or chain-of-thought in persistence;
- user and administrator Vue permissions and display states;
- migration idempotency and existing-memory compatibility.

## 14. Rollout

1. Introduce memory-type-specific automatic-confirmation policy without changing runtime recall.
2. Separate personal and domain administration permissions and UI.
3. Add structured procedural storage and migrate existing procedures to `legacy-v1`.
4. Generate structured personal Bug procedure candidates behind a feature flag.
5. Enable procedure-guided Bug Graph routing in observe-only mode and compare decisions.
6. Enable procedure guidance after regression thresholds pass.
7. Add domain promotion and domain procedure recall.
8. Add conflict detection, expiry review, and aggregated operational metrics.

Each stage retains an independent feature flag and can be disabled without deleting stored memory.

## 15. Non-Goals

- Storing or exposing model chain-of-thought.
- Replaying raw model tool calls or credentials.
- Allowing memory to execute write operations.
- Automatically promoting personal content to a domain.
- Replacing Chroma knowledge retrieval, current code inspection, Swagger, or logs.
- Introducing Redis, Celery, PostgreSQL, Neo4j, or a separate vector database in the first release.
