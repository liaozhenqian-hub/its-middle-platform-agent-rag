import type { KnowledgeSource } from "@/types/api";

type SourceSummary = Pick<KnowledgeSource, "source_type" | "config">;

export interface SourceEnvironmentPresentation {
  branch: string;
  note: string;
}

export function sourceEnvironmentPresentation(
  source: SourceSummary,
): SourceEnvironmentPresentation | null {
  if (source.source_type !== "git") return null;
  const branch = source.config.branch;
  if (typeof branch !== "string" || !branch.trim()) return null;
  const projectPath =
    typeof source.config.project_path === "string" ? source.config.project_path : "";
  const layer = projectPath.endsWith("-web") ? "前端" : "后端";
  const normalizedBranch = branch.trim();
  const environment =
    normalizedBranch === "master"
      ? "线上"
      : normalizedBranch === "develop"
        ? "开发 / 测试"
        : "代码分支 · ";
  return {
    branch: normalizedBranch,
    note: `${environment}${layer}`,
  };
}

export function checkpointForSource(source: SourceSummary): string {
  const key = source.source_type === "git" ? "last_synced_commit" : "last_synced_version";
  const value = source.config[key];
  return typeof value === "string" && value ? value : "未同步";
}

export interface GitProjectSelection {
  name: string;
  project_id: string;
  project_path: string;
  project_url: string;
  project_web_url: string;
  branch: string;
}

export type GitDomainId = "metric-platform" | "approval-flow" | "workflow";
export type GitDomainPatterns = Record<GitDomainId, string[]>;

export const GIT_DOMAIN_IDS: readonly GitDomainId[] = [
  "metric-platform",
  "approval-flow",
  "workflow",
];

const GIT_RULE_PRESETS: Record<string, GitDomainPatterns> = {
  "erp/loctek-middle-platform": {
    "metric-platform": [
      "docs/datacenter/**",
      "**/datacenter/**",
      "**/metric/**",
      "**/cube/**",
      "skills/mp-backend-datacenter-*/**",
    ],
    "approval-flow": [
      "docs/flow/**",
      "**/flow/**",
      "**/common-flowable/**",
      "**/larkApprove/**",
      "**/lark/approve/**",
      "skills/mp-backend-approval-flow/**",
    ],
    workflow: [
      "**/workflow/**",
      "**/common-liteflow/**",
      "skills/mp-backend-workflow/**",
    ],
  },
  "erp/loctek-middle-platform-web": {
    "metric-platform": [
      "src/views/digitalIntelligenceCenter/indicatorPlatform/**",
      "src/api/digitalIntelligenceCenter/**",
      ".trae/skills/complex-indicator-handoff/**",
    ],
    "approval-flow": [
      "src/views/approvalCenter/**",
      "src/api/process/**",
      "src/stores/approval.js",
      "src/components/drawer/approverDrawer.vue",
    ],
    workflow: [
      "src/views/integrationCenter/workflow/**",
      "src/api/integrationCenter/workflow/**",
      ".trae/skills/workflow-branch-chain/**",
      "src/css/workflow.css",
      "src/utils/workflow.js",
    ],
  },
};

export function gitRulePreset(projectPath: string): GitDomainPatterns {
  const preset = GIT_RULE_PRESETS[projectPath];
  return Object.fromEntries(
    GIT_DOMAIN_IDS.map((domainId) => [domainId, [...(preset?.[domainId] ?? [])]]),
  ) as GitDomainPatterns;
}

export function createGitPayload(
  project: GitProjectSelection,
  patterns: GitDomainPatterns,
) {
  const rules = GIT_DOMAIN_IDS.flatMap((domain_id) =>
    [...new Set(patterns[domain_id].map((pattern) => pattern.trim()).filter(Boolean))].map(
      (pattern) => ({ pattern, domain_id }),
    ),
  );
  return {
    ...project,
    rules: rules.map((rule, index) => ({ ...rule, priority: (index + 1) * 10 })),
  };
}

export function secretFieldsAfterSubmit() {
  return { password: "", bearer_token: "" };
}
