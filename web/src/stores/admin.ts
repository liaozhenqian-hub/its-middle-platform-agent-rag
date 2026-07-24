import { defineStore } from "pinia";

import { api } from "@/api";
import type {
  GitLabBranch,
  GitLabProject,
  KnowledgeDomain,
  KnowledgeSource,
  KnowledgeSpace,
  SyncJob,
} from "@/types/api";

export interface GitSourcePayload {
  name: string;
  project_id: string;
  project_path: string;
  project_url: string;
  project_web_url: string;
  branch: string;
  rules: Array<{ pattern: string; domain_id: string; priority: number }>;
}

export interface SwaggerSourcePayload {
  name: string;
  domain_id: string;
  url: string;
  auth_type: "none" | "basic" | "bearer";
  username: string;
  password: string;
  bearer_token: string;
  timeout_seconds: number;
}

export interface DocumentSourceInput {
  name: string;
  domain_id: string;
  version: string;
  upload: File;
}

interface GitSourceResponse {
  source: KnowledgeSource;
  webhook_secret: string;
}

interface DocumentSourceResponse {
  source: KnowledgeSource;
  job: SyncJob;
}

function messageFrom(error: unknown, fallback: string): string {
  return error instanceof Error ? error.message : fallback;
}

function upsertFirst<T extends { id: string }>(items: T[], item: T): T[] {
  return [item, ...items.filter((existing) => existing.id !== item.id)];
}

export const useAdminStore = defineStore("admin", {
  state: () => ({
    spaces: [] as KnowledgeSpace[],
    sources: [] as KnowledgeSource[],
    jobs: [] as SyncJob[],
    projects: [] as GitLabProject[],
    branches: [] as GitLabBranch[],
    spacesLoading: false,
    sourcesLoading: false,
    jobsLoading: false,
    projectsLoading: false,
    branchesLoading: false,
    actionLoading: false,
    dashboardInitialized: false,
    spacesError: "",
    sourcesError: "",
    jobsError: "",
    discoveryError: "",
    actionError: "",
    jobPollTimer: null as number | null,
  }),
  getters: {
    domains: (state): KnowledgeDomain[] =>
      state.spaces.flatMap((space) => space.domains).sort((a, b) => a.sort_order - b.sort_order),
    dashboardLoading: (state): boolean =>
      state.spacesLoading || state.sourcesLoading || state.jobsLoading,
    dashboardError: (state): string =>
      state.spacesError || state.sourcesError || state.jobsError,
  },
  actions: {
    async loadDashboard() {
      await Promise.all([this.loadSpaces(), this.loadSources(), this.loadJobs()]);
      this.dashboardInitialized = true;
    },
    async loadSpaces() {
      this.spacesLoading = true;
      this.spacesError = "";
      try {
        this.spaces = await api.get<KnowledgeSpace[]>("/v1/knowledge/spaces");
      } catch (error) {
        this.spacesError = messageFrom(error, "Unable to load knowledge domains");
      } finally {
        this.spacesLoading = false;
      }
    },
    async loadSources() {
      this.sourcesLoading = true;
      this.sourcesError = "";
      try {
        this.sources = await api.get<KnowledgeSource[]>("/v1/admin/sources");
      } catch (error) {
        this.sourcesError = messageFrom(error, "Unable to load knowledge sources");
      } finally {
        this.sourcesLoading = false;
      }
    },
    async loadJobs(silent = false) {
      if (this.jobsLoading) return;
      this.jobsLoading = !silent;
      this.jobsError = "";
      try {
        this.jobs = await api.get<SyncJob[]>("/v1/admin/jobs");
      } catch (error) {
        this.jobsError = messageFrom(error, "Unable to load sync jobs");
      } finally {
        this.jobsLoading = false;
      }
    },
    async searchGitProjects(search: string) {
      const query = search.trim();
      if (!query) {
        this.projects = [];
        return;
      }
      this.projectsLoading = true;
      this.discoveryError = "";
      try {
        this.projects = await api.get<GitLabProject[]>(
          `/v1/admin/gitlab/projects?search=${encodeURIComponent(query)}`,
        );
      } catch (error) {
        this.discoveryError = messageFrom(error, "Unable to search GitLab projects");
      } finally {
        this.projectsLoading = false;
      }
    },
    async loadGitBranches(projectId: number | string, search = "") {
      this.branchesLoading = true;
      this.discoveryError = "";
      try {
        this.branches = await api.get<GitLabBranch[]>(
          `/v1/admin/gitlab/projects/${encodeURIComponent(String(projectId))}/branches?search=${encodeURIComponent(search)}`,
        );
      } catch (error) {
        this.discoveryError = messageFrom(error, "Unable to load GitLab branches");
      } finally {
        this.branchesLoading = false;
      }
    },
    async createGitSource(payload: GitSourcePayload): Promise<GitSourceResponse> {
      return this.runAction(async () => {
        const result = await api.post<GitSourceResponse>("/v1/admin/sources/git", payload);
        this.sources = upsertFirst(this.sources, result.source);
        return result;
      });
    },
    async createSwaggerSource(payload: SwaggerSourcePayload): Promise<KnowledgeSource> {
      return this.runAction(async () => {
        const result = await api.post<KnowledgeSource>("/v1/admin/sources/swagger", payload);
        this.sources = upsertFirst(this.sources, result);
        return result;
      });
    },
    async createDocumentSource(input: DocumentSourceInput): Promise<DocumentSourceResponse> {
      return this.runAction(async () => {
        const body = new FormData();
        body.append("name", input.name);
        body.append("domain_id", input.domain_id);
        body.append("version", input.version);
        body.append("upload", input.upload);
        const result = await api.post<DocumentSourceResponse>(
          "/v1/admin/sources/documents",
          body,
        );
        this.sources = upsertFirst(this.sources, result.source);
        this.jobs = upsertFirst(this.jobs, result.job);
        return result;
      });
    },
    async syncSource(source: KnowledgeSource): Promise<SyncJob> {
      if (source.source_type !== "git") {
        throw new Error("Only Git sources can be synchronized");
      }
      return this.runAction(async () => {
        const result = await api.post<SyncJob>(
          `/v1/admin/sources/${encodeURIComponent(source.id)}/sync`,
        );
        this.jobs = upsertFirst(this.jobs, result);
        return result;
      });
    },
    async deleteSource(source: KnowledgeSource, confirmName: string): Promise<SyncJob> {
      if (confirmName !== source.name) throw new Error("Source name does not match");
      return this.runAction(async () => {
        const result = await api.delete<SyncJob>(
          `/v1/admin/sources/${encodeURIComponent(source.id)}`,
          { confirm_name: confirmName },
        );
        this.sources = this.sources.map((item) =>
          item.id === source.id
            ? {
                ...item,
                enabled: false,
                config: { ...item.config, lifecycle_state: "deleting" },
              }
            : item,
        );
        this.jobs = upsertFirst(this.jobs, result);
        return result;
      });
    },
    async retryJob(job: SyncJob): Promise<SyncJob> {
      if (job.state !== "failed") throw new Error("Only failed jobs can be retried");
      return this.runAction(async () => {
        const result = await api.post<SyncJob>(
          `/v1/admin/jobs/${encodeURIComponent(job.id)}/retry`,
        );
        this.jobs = upsertFirst(this.jobs, result);
        return result;
      });
    },
    startJobPolling(intervalMs = 3_000) {
      this.stopJobPolling();
      this.jobPollTimer = window.setInterval(() => void this.loadJobs(true), intervalMs);
    },
    stopJobPolling() {
      if (this.jobPollTimer !== null) window.clearInterval(this.jobPollTimer);
      this.jobPollTimer = null;
    },
    async runAction<T>(operation: () => Promise<T>): Promise<T> {
      this.actionLoading = true;
      this.actionError = "";
      try {
        return await operation();
      } catch (error) {
        this.actionError = messageFrom(error, "Admin operation failed");
        throw error;
      } finally {
        this.actionLoading = false;
      }
    },
  },
});
