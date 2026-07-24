import { defineStore } from "pinia";

import { api, setCsrfToken } from "@/api";
import { ApiError } from "@/api/client";
import type { AdminIdentity } from "@/types/api";

export const useAuthStore = defineStore("auth", {
  state: () => ({
    identity: null as AdminIdentity | null,
    initialized: false,
    loading: false,
    error: "",
  }),
  getters: {
    authenticated: (state) => state.identity !== null,
  },
  actions: {
    applyIdentity(identity: AdminIdentity | null) {
      this.identity = identity;
      setCsrfToken(identity?.csrf_token ?? null);
    },
    async login(username: string, password: string) {
      this.loading = true;
      this.error = "";
      try {
        const identity = await api.post<AdminIdentity>("/v1/admin/auth/login", {
          username,
          password,
        });
        this.applyIdentity(identity);
        this.initialized = true;
      } catch (error) {
        this.error = error instanceof Error ? error.message : "登录失败";
        throw error;
      } finally {
        this.loading = false;
      }
    },
    async restore(): Promise<boolean> {
      if (this.initialized) return this.authenticated;
      this.loading = true;
      this.error = "";
      try {
        this.applyIdentity(await api.get<AdminIdentity>("/v1/admin/auth/me"));
      } catch (error) {
        this.applyIdentity(null);
        if (!(error instanceof ApiError && error.status === 401)) {
          this.error = error instanceof Error ? error.message : "无法验证管理员会话";
        }
      } finally {
        this.initialized = true;
        this.loading = false;
      }
      return this.authenticated;
    },
    async logout() {
      this.loading = true;
      this.error = "";
      try {
        await api.post<void>("/v1/admin/auth/logout");
      } finally {
        this.applyIdentity(null);
        this.initialized = true;
        this.loading = false;
      }
    },
  },
});
