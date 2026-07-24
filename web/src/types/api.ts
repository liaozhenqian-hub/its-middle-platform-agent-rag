export type CitationSourceType =
  | "knowledge_chunk"
  | "mcp_tool"
  | "code"
  | "product_document"
  | "swagger"
  | "log_trace";

export interface Citation {
  source_type: CitationSourceType;
  source_id: string;
  title: string;
  domain: string;
  metadata: Record<string, unknown>;
}

export interface CitationDetail {
  source_type: CitationSourceType;
  source_id: string;
  title: string;
  domain: string;
  excerpt: string;
  language: string | null;
  truncated: boolean;
  metadata: Record<string, unknown>;
  content_scope?: "excerpt" | "section" | "full";
  full_text_available?: boolean;
  document_url?: string | null;
}

export interface ToolRun {
  tool_call_id: string;
  tool_name: string;
  agent_name: string;
  status: string;
  duration_ms: number | null;
  arguments: Record<string, unknown>;
}

export interface AgentResponse {
  status: "completed" | "approval_required";
  conversation_id: string;
  run_id: string;
  answer: string | null;
  last_agent: string;
  routed_domains: string[];
  specialists_used: string[];
  citations: Citation[];
  tool_runs: ToolRun[];
  approvals: Array<{ tool_call_id: string; tool_name: string; status: string }>;
  trace_id: string | null;
  quality_turn_id: string | null;
  feedback_token: string | null;
}

export type QualityStatus =
  | "running"
  | "completed"
  | "approval_required"
  | "clarification_required"
  | "no_answer"
  | "error"
  | "timeout"
  | "cancelled"
  | "interrupted";

export interface QualityFeedback {
  id: string;
  turn_id: string;
  channel: "web" | "api" | "feishu" | "codex";
  user_id: string | null;
  user_name: string | null;
  rating: "positive" | "negative";
  reason: string;
  reason_code: string;
  created_at: string;
  updated_at: string;
}

export interface QualityTurn {
  id: string;
  run_id: string;
  conversation_id: string;
  channel: "web" | "api" | "feishu" | "eval" | "codex";
  channel_message_id: string | null;
  channel_reply_message_id: string | null;
  user_id: string | null;
  user_name: string | null;
  chat_id: string | null;
  question: string;
  answer: string | null;
  knowledge_space_id: string;
  domain_id: string | null;
  status: QualityStatus;
  provider: string;
  model_name: string;
  last_agent: string;
  application_version: string;
  prompt_version: string;
  duration_ms: number | null;
  error_type: string | null;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
  tools: ToolRun[];
  citations: Citation[];
  feedback: QualityFeedback[];
  routed_domains: string[];
  specialists_used: string[];
}

export interface QualityAnalytics {
  total_turns: number;
  completed_turns: number;
  citation_coverage: number;
  average_tool_calls: number;
  feedback_rate: number;
  p50_duration_ms: number | null;
  p90_duration_ms: number | null;
  issue_counts: Record<string, number>;
}

export interface QualityAnnotation {
  id: string;
  turn_id: string;
  source: "rule" | "judge" | "manual";
  code: string;
  severity: "info" | "warning" | "error" | "critical";
  confidence: number;
  details: Record<string, unknown>;
  review_status: "pending" | "confirmed" | "dismissed";
  reviewer: string | null;
  reviewed_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface QualityAnnotationPage {
  items: QualityAnnotation[];
  page: number;
  page_size: number;
  total: number;
}

export interface QualityTurnPage {
  items: QualityTurn[];
  page: number;
  page_size: number;
  total: number;
}

export interface EvalCase {
  id: string;
  source_turn_id: string | null;
  name: string;
  question: string;
  knowledge_space_id: string;
  domain_id: string | null;
  required_tools: string[];
  required_citation_types: string[];
  required_facts: string[];
  forbidden_facts: string[];
  tags: string[];
  enabled: boolean;
  expected_behavior: "answer" | "clarify" | "refuse";
  max_latency_ms: number;
  max_tool_calls: number;
  max_citations: number;
  created_at: string;
  updated_at: string;
  turns: string[];
  task_type: string;
  suite: string;
  priority: string;
  approval_state: string;
  version: number;
}

export interface EvalCasePayload {
  name: string;
  required_tools: string[];
  required_citation_types: string[];
  required_facts: string[];
  forbidden_facts: string[];
  tags: string[];
  enabled: boolean;
  turns?: string[];
  task_type?: string;
  suite?: string;
  priority?: string;
  approval_state?: string;
}

export interface EvalRun {
  id: string;
  status: string;
  application_version: string;
  provider: string;
  model_name: string;
  total_cases: number;
  passed_cases: number;
  failed_cases: number;
  created_at: string;
  completed_at: string | null;
  case_ids: string[];
  config_snapshot: Record<string, unknown>;
  cancel_requested: boolean;
  current_case: number;
}

export interface EvalResult {
  id: string;
  run_id: string;
  case_id: string;
  status: string;
  answer: string | null;
  last_agent: string;
  tool_names: string[];
  citation_types: string[];
  duration_ms: number | null;
  checks: Record<string, boolean>;
  passed: boolean;
  error_type: string | null;
  created_at: string;
  judge_score: number | null;
  judge: Record<string, unknown>;
  failure_codes: string[];
  review_state: string;
  case_snapshot: Record<string, unknown>;
}

export interface KnowledgeDomain {
  id: string;
  name: string;
  sort_order: number;
}

export interface KnowledgeSpace {
  id: string;
  name: string;
  domains: KnowledgeDomain[];
}

export interface ConversationHistoryMessage {
  id: number;
  role: "user" | "assistant";
  content: string;
  created_at: string;
}

export interface ConversationHistoryItem {
  conversation_id: string;
  title: string;
  preview: string;
  channel: string;
  message_count: number;
  created_at: string;
  updated_at: string;
}

export interface ConversationHistoryPage {
  items: ConversationHistoryItem[];
  total: number;
  page: number;
  page_size: number;
}

export interface ConversationHistoryDetail {
  conversation_id: string;
  title: string;
  channel: string;
  knowledge_space_id: string | null;
  domain_id: string | null;
  created_at: string;
  updated_at: string;
  messages: ConversationHistoryMessage[];
}

export interface AdminIdentity {
  username: string;
  csrf_token: string;
  expires_at: string;
}

export type MemoryScope = "user" | "conversation" | "team" | "domain" | "global";
export type MemoryType =
  | "user_preference"
  | "user_context"
  | "episodic_memory"
  | "decision_memory"
  | "procedural_memory";

export interface MemoryCandidate {
  id: string;
  scope_type: MemoryScope;
  owner_id: string;
  space_id: string;
  domain_id: string | null;
  memory_type: MemoryType;
  subject: string;
  normalized_fact: string;
  summary: string;
  source_turn_id: string | null;
  source_citations: string[];
  confidence: number;
  status: "candidate" | "approved" | "rejected" | "expired";
  expires_at: string | null;
  created_at: string;
  updated_at: string;
  auto_confirm_eligible?: boolean;
  auto_confirm_at?: string | null;
}

export interface PersonalMemoryStatistics {
  candidate: Partial<Record<MemoryType, number>>;
  confirmed: Partial<Record<MemoryType, number>>;
  rejected: Partial<Record<MemoryType, number>>;
  deleted: Partial<Record<MemoryType, number>>;
}

export interface DomainMemoryPromotion {
  id: string;
  target_domain_id: string;
  public_summary: string;
  state: "pending" | "approved" | "rejected";
  valid_until: string | null;
  created_at: string;
}

export interface LongTermMemory {
  id: string;
  scope_type: MemoryScope;
  owner_id: string;
  space_id: string;
  domain_id: string | null;
  memory_type: MemoryType;
  subject: string;
  normalized_fact: string;
  summary: string;
  source_turn_id: string | null;
  source_citations: string[];
  confidence: number;
  status: "confirmed" | "expired" | "deleted";
  valid_from: string;
  valid_until: string | null;
  last_used_at: string | null;
  supersedes_id: string | null;
  created_at: string;
  updated_at: string;
}

export interface UserIdentity {
  owner_id: string;
  identity_kind: "anonymous" | "feishu" | "personal_token";
  authenticated: boolean;
  display_name: string;
  csrf_token: string | null;
  scopes: string[];
  merge_available: boolean;
  feishu_login_available: boolean;
  feishu_login_url: string;
}

export interface IdentityMergePreview {
  available: boolean;
  memories?: number;
  candidates?: number;
  conversations?: number;
  duplicates?: number;
  conflicts?: number;
  unique?: number;
}

export interface PersonalApiToken {
  id: string;
  name: string;
  display_prefix: string;
  scopes: string[];
  created_at: string;
  last_used_at: string | null;
  revoked_at: string | null;
}

export type SourceType = "git" | "document" | "swagger";

export interface KnowledgeSource {
  id: string;
  space_id: string;
  domain_id: string | null;
  source_type: SourceType;
  name: string;
  config: Record<string, unknown>;
  enabled: boolean;
  credential_configured: boolean;
  created_at: string;
  updated_at: string;
}

export type JobState = "queued" | "running" | "succeeded" | "failed";

export interface SyncJob {
  id: string;
  source_id: string;
  kind: string;
  state: JobState;
  target_commit: string | null;
  attempt: number;
  error: string | null;
  worker_id: string | null;
  available_at: string;
  claimed_at: string | null;
  finished_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface GitLabProject {
  id: number | string;
  path_with_namespace: string;
  name: string;
  web_url: string;
  default_branch: string;
}

export interface GitLabBranch {
  name: string;
  commit_sha: string;
}
