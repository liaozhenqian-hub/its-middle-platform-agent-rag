import { defineStore } from "pinia";

import { api } from "@/api";
import type {
  IdentityMergePreview,
  PersonalApiToken,
  UserIdentity,
} from "@/types/api";

export const useUserIdentityStore = defineStore("user-identity", {
  state: () => ({
    identity: null as UserIdentity | null,
    mergePreview: null as IdentityMergePreview | null,
    tokens: [] as PersonalApiToken[],
    createdToken: "",
    loading: false,
    error: "",
  }),
  getters: {
    authenticated: (state) => state.identity?.identity_kind === "feishu",
    csrfHeaders: (state): Record<string, string> =>
      state.identity?.csrf_token
        ? { "X-User-CSRF-Token": state.identity.csrf_token }
        : {},
  },
  actions: {
    async load() {
      this.loading = true;
      this.error = "";
      try {
        this.identity = await api.get<UserIdentity>("/v1/auth/me");
        if (this.identity.merge_available) await this.loadMergePreview();
      } catch (error) {
        this.error = error instanceof Error ? error.message : "无法加载当前身份";
      } finally {
        this.loading = false;
      }
    },
    login() {
      window.location.assign(
        this.identity?.feishu_login_url ?? "/api/v1/auth/feishu/start",
      );
    },
    async logout() {
      await api.post<void>("/v1/auth/logout", undefined, {
        headers: this.csrfHeaders,
      });
      this.identity = null;
      this.tokens = [];
      this.mergePreview = null;
      this.createdToken = "";
      await this.load();
    },
    async loadMergePreview() {
      this.mergePreview = await api.get<IdentityMergePreview>(
        "/v1/auth/merge-preview",
      );
    },
    async mergeAnonymous(confirm: boolean) {
      await api.post(
        "/v1/auth/merge-anonymous",
        { confirm },
        { headers: this.csrfHeaders },
      );
      this.mergePreview = { available: false };
      if (this.identity) this.identity.merge_available = false;
    },
    async loadTokens() {
      this.tokens = await api.get<PersonalApiToken[]>("/v1/account/tokens");
    },
    async createToken(name: string, scopes: string[]) {
      const result = await api.post<{
        token: string;
        item: PersonalApiToken;
      }>(
        "/v1/account/tokens",
        { name, scopes },
        { headers: this.csrfHeaders },
      );
      this.createdToken = result.token;
      this.tokens.unshift(result.item);
      return result.token;
    },
    clearCreatedToken() {
      this.createdToken = "";
    },
    async revokeToken(id: string) {
      await api.delete<void>(`/v1/account/tokens/${encodeURIComponent(id)}`, undefined, {
        headers: this.csrfHeaders,
      });
      const token = this.tokens.find((item) => item.id === id);
      if (token) token.revoked_at = new Date().toISOString();
    },
  },
});
