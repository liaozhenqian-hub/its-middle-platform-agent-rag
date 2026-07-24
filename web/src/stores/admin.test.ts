import { createPinia, setActivePinia } from "pinia";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  delete: vi.fn(),
  get: vi.fn(),
  post: vi.fn(),
}));

vi.mock("@/api", () => ({
  api: {
    delete: mocks.delete,
    get: mocks.get,
    post: mocks.post,
  },
}));

import type { KnowledgeSource, SyncJob } from "@/types/api";
import { useAdminStore } from "./admin";

function source(overrides: Partial<KnowledgeSource> = {}): KnowledgeSource {
  return {
    id: "source-1",
    space_id: "middle-platform",
    domain_id: null,
    source_type: "git",
    name: "Middle platform",
    config: { last_synced_commit: "abc123" },
    enabled: true,
    credential_configured: true,
    created_at: "2026-07-15T00:00:00Z",
    updated_at: "2026-07-15T00:00:00Z",
    ...overrides,
  };
}

function job(overrides: Partial<SyncJob> = {}): SyncJob {
  return {
    id: "job-1",
    source_id: "source-1",
    kind: "manual",
    state: "queued",
    target_commit: null,
    attempt: 0,
    error: null,
    worker_id: null,
    available_at: "2026-07-15T00:00:00Z",
    claimed_at: null,
    finished_at: null,
    created_at: "2026-07-15T00:00:00Z",
    updated_at: "2026-07-15T00:00:00Z",
    ...overrides,
  };
}

describe("admin store", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.clearAllMocks();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("loads knowledge spaces, sources, and jobs for the dashboard", async () => {
    const gitSource = source();
    const queuedJob = job();
    mocks.get.mockImplementation(async (path: string) => {
      if (path === "/v1/knowledge/spaces") {
        return [
          {
            id: "middle-platform",
            name: "Middle platform",
            domains: [{ id: "metric-platform", name: "Metrics", sort_order: 10 }],
          },
        ];
      }
      if (path === "/v1/admin/sources") return [gitSource];
      if (path === "/v1/admin/jobs") return [queuedJob];
      throw new Error(`unexpected GET ${path}`);
    });
    const store = useAdminStore();

    await store.loadDashboard();

    expect(mocks.get).toHaveBeenCalledWith("/v1/knowledge/spaces");
    expect(mocks.get).toHaveBeenCalledWith("/v1/admin/sources");
    expect(mocks.get).toHaveBeenCalledWith("/v1/admin/jobs");
    expect(store.sources).toEqual([gitSource]);
    expect(store.jobs).toEqual([queuedJob]);
    expect(store.domains).toEqual([{ id: "metric-platform", name: "Metrics", sort_order: 10 }]);
    expect(store.dashboardLoading).toBe(false);
  });

  it("encodes GitLab project and branch discovery queries", async () => {
    mocks.get
      .mockResolvedValueOnce([
        {
          id: 42,
          path_with_namespace: "platform/middle",
          name: "Middle",
          web_url: "https://gitlab.example/platform/middle",
          default_branch: "main",
        },
      ])
      .mockResolvedValueOnce([{ name: "release/2026 Q3", commit_sha: "def456" }]);
    const store = useAdminStore();

    await store.searchGitProjects("middle platform");
    await store.loadGitBranches("group/project", "release/2026 Q3");

    expect(mocks.get).toHaveBeenNthCalledWith(
      1,
      "/v1/admin/gitlab/projects?search=middle%20platform",
    );
    expect(mocks.get).toHaveBeenNthCalledWith(
      2,
      "/v1/admin/gitlab/projects/group%2Fproject/branches?search=release%2F2026%20Q3",
    );
    expect(store.projects[0]?.id).toBe(42);
    expect(store.branches[0]?.name).toBe("release/2026 Q3");
  });

  it("creates Git and Swagger sources with the supplied JSON payloads", async () => {
    const gitSource = source();
    const swaggerSource = source({
      id: "swagger-1",
      domain_id: "metric-platform",
      source_type: "swagger",
      name: "Metrics API",
    });
    mocks.post
      .mockResolvedValueOnce({ source: gitSource, webhook_secret: "one-time-secret" })
      .mockResolvedValueOnce(swaggerSource);
    const store = useAdminStore();
    const gitPayload = {
      name: "Middle platform",
      project_id: "42",
      project_path: "platform/middle",
      project_url: "https://gitlab.example/platform/middle.git",
      project_web_url: "https://gitlab.example/platform/middle",
      branch: "main",
      rules: [
        { pattern: "**/metric/**", domain_id: "metric-platform", priority: 100 },
        { pattern: "**/approval/**", domain_id: "approval-flow", priority: 100 },
        { pattern: "**/workflow/**", domain_id: "workflow", priority: 100 },
      ],
    };
    const swaggerPayload = {
      name: "Metrics API",
      domain_id: "metric-platform",
      url: "https://swagger.internal/openapi.json",
      auth_type: "bearer" as const,
      username: "",
      password: "",
      bearer_token: "secret-token",
      timeout_seconds: 15,
    };

    const created = await store.createGitSource(gitPayload);
    await store.createSwaggerSource(swaggerPayload);

    expect(mocks.post).toHaveBeenNthCalledWith(1, "/v1/admin/sources/git", gitPayload);
    expect(mocks.post).toHaveBeenNthCalledWith(2, "/v1/admin/sources/swagger", swaggerPayload);
    expect(created.webhook_secret).toBe("one-time-secret");
    expect(store.sources).toEqual([swaggerSource, gitSource]);
  });

  it("builds the exact multipart document upload", async () => {
    const documentSource = source({
      id: "document-1",
      domain_id: "metric-platform",
      source_type: "document",
      name: "Metrics docs",
    });
    const documentJob = job({ id: "document-job", source_id: "document-1", kind: "document" });
    mocks.post.mockResolvedValue({ source: documentSource, job: documentJob });
    const upload = new File(["zip-content"], "documents.zip", { type: "application/zip" });
    const store = useAdminStore();

    await store.createDocumentSource({
      name: "Metrics docs",
      domain_id: "metric-platform",
      version: "v1.0",
      upload,
    });

    expect(mocks.post).toHaveBeenCalledTimes(1);
    const [path, body] = mocks.post.mock.calls[0] as [string, FormData];
    expect(path).toBe("/v1/admin/sources/documents");
    expect(body).toBeInstanceOf(FormData);
    expect(body.get("name")).toBe("Metrics docs");
    expect(body.get("domain_id")).toBe("metric-platform");
    expect(body.get("version")).toBe("v1.0");
    expect(body.get("upload")).toBe(upload);
    expect(store.jobs).toEqual([documentJob]);
  });

  it("allows sync only for Git and sends exact-name deletion confirmation", async () => {
    const gitSource = source();
    const documentSource = source({ source_type: "document", id: "document-1" });
    const syncJob = job();
    const deleteJob = job({ id: "delete-job", kind: "delete" });
    mocks.post.mockResolvedValue(syncJob);
    mocks.delete.mockResolvedValue(deleteJob);
    const store = useAdminStore();
    store.sources = [gitSource];

    await store.syncSource(gitSource);
    await expect(store.syncSource(documentSource)).rejects.toThrow(
      "Only Git sources can be synchronized",
    );
    await expect(store.deleteSource(gitSource, "wrong name")).rejects.toThrow(
      "Source name does not match",
    );
    await store.deleteSource(gitSource, gitSource.name);

    expect(mocks.post).toHaveBeenCalledWith("/v1/admin/sources/source-1/sync");
    expect(mocks.delete).toHaveBeenCalledWith("/v1/admin/sources/source-1", {
      confirm_name: "Middle platform",
    });
    expect(store.sources[0]?.enabled).toBe(false);
    expect(store.jobs[0]?.kind).toBe("delete");
  });

  it("retries failed jobs and polls the job list every three seconds", async () => {
    vi.useFakeTimers();
    const failedJob = job({ state: "failed", error: "clone failed" });
    const queuedJob = job({ state: "queued", error: null });
    mocks.post.mockResolvedValue(queuedJob);
    mocks.get.mockResolvedValue([queuedJob]);
    const store = useAdminStore();

    await store.retryJob(failedJob);
    await expect(store.retryJob(queuedJob)).rejects.toThrow("Only failed jobs can be retried");
    store.startJobPolling();
    await vi.advanceTimersByTimeAsync(6_000);
    store.stopJobPolling();
    await vi.advanceTimersByTimeAsync(3_000);

    expect(mocks.post).toHaveBeenCalledWith("/v1/admin/jobs/job-1/retry");
    expect(mocks.get).toHaveBeenCalledTimes(2);
    expect(store.jobPollTimer).toBeNull();
  });

  it("exposes load errors and always clears loading state", async () => {
    mocks.get.mockRejectedValue(new Error("service unavailable"));
    const store = useAdminStore();

    await store.loadSources();

    expect(store.sourcesError).toBe("service unavailable");
    expect(store.sourcesLoading).toBe(false);
  });
});
