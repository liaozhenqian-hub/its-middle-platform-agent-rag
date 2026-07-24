import { describe, expect, it } from "vitest";

import {
  checkpointForSource,
  createGitPayload,
  gitRulePreset,
  secretFieldsAfterSubmit,
  sourceEnvironmentPresentation,
} from "./sources";

describe("source utilities", () => {
  it("extracts source-specific checkpoints", () => {
    expect(checkpointForSource({ source_type: "git", config: { last_synced_commit: "abc123" } })).toBe(
      "abc123",
    );
    expect(
      checkpointForSource({ source_type: "document", config: { last_synced_version: "v7" } }),
    ).toBe("v7");
    expect(checkpointForSource({ source_type: "swagger", config: {} })).toBe("未同步");
  });

  it("distinguishes master and develop Git source environments", () => {
    expect(
      sourceEnvironmentPresentation({
        source_type: "git",
        config: { branch: "master", project_path: "erp/loctek-middle-platform" },
      }),
    ).toEqual({ branch: "master", note: "线上后端" });
    expect(
      sourceEnvironmentPresentation({
        source_type: "git",
        config: { branch: "master", project_path: "erp/loctek-middle-platform-web" },
      }),
    ).toEqual({ branch: "master", note: "线上前端" });
    expect(
      sourceEnvironmentPresentation({
        source_type: "git",
        config: { branch: "develop", project_path: "erp/loctek-middle-platform" },
      }),
    ).toEqual({ branch: "develop", note: "开发 / 测试后端" });
    expect(
      sourceEnvironmentPresentation({ source_type: "document", config: {} }),
    ).toBeNull();
  });

  it("provides real multi-domain presets for both middle-platform repositories", () => {
    const backend = gitRulePreset("erp/loctek-middle-platform");
    expect(backend["metric-platform"]).toContain("**/datacenter/**");
    expect(backend["approval-flow"]).toContain("**/common-flowable/**");
    expect(backend.workflow).toContain("**/common-liteflow/**");

    const frontend = gitRulePreset("erp/loctek-middle-platform-web");
    expect(frontend["metric-platform"]).toContain(
      "src/views/digitalIntelligenceCenter/indicatorPlatform/**",
    );
    expect(frontend["approval-flow"]).toContain("src/views/approvalCenter/**");
    expect(frontend.workflow).toContain("src/views/integrationCenter/workflow/**");

    expect(gitRulePreset("other/project")).toEqual({
      "metric-platform": [],
      "approval-flow": [],
      workflow: [],
    });
  });

  it("normalizes and flattens multi-pattern Git rules", () => {
    const payload = createGitPayload(
      {
        name: "中台代码",
        project_id: "42",
        project_path: "platform/middle",
        project_url: "https://gitlab.example/platform/middle.git",
        project_web_url: "https://gitlab.example/platform/middle",
        branch: "main",
      },
      {
        "metric-platform": [" **/metric/** ", "**/metric/**", "**/cube/**"],
        "approval-flow": ["**/flow/**"],
        workflow: ["**/workflow/**"],
      },
    );

    expect(payload.rules).toHaveLength(4);
    expect(payload.rules.map((rule) => rule.domain_id)).toEqual([
      "metric-platform",
      "metric-platform",
      "approval-flow",
      "workflow",
    ]);
    expect(payload.rules.map((rule) => rule.pattern)).toEqual([
      "**/metric/**",
      "**/cube/**",
      "**/flow/**",
      "**/workflow/**",
    ]);
    expect(payload.rules.map((rule) => rule.priority)).toEqual([10, 20, 30, 40]);
  });

  it("clears Swagger secrets after submission", () => {
    expect(secretFieldsAfterSubmit()).toEqual({ password: "", bearer_token: "" });
  });
});
