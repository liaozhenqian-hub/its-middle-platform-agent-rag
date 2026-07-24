import { createRouter, createWebHistory } from "vue-router";

import { createAdminGuard } from "./guard";
import { useAuthStore } from "@/stores/auth";
import { pinia } from "@/stores/pinia";

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: "/", redirect: "/chat" },
    {
      path: "/chat",
      name: "chat",
      component: () => import("@/views/ChatView.vue"),
    },
    {
      path: "/memory",
      name: "memory",
      component: () => import("@/views/MemoryView.vue"),
    },
    {
      path: "/history",
      name: "history",
      component: () => import("@/views/HistoryView.vue"),
    },
    {
      path: "/account",
      name: "account",
      component: () => import("@/views/AccountView.vue"),
    },
    {
      path: "/admin/login",
      name: "admin-login",
      component: () => import("@/views/AdminLoginView.vue"),
    },
    {
      path: "/admin",
      name: "admin",
      meta: { requiresAdmin: true },
      component: () => import("@/views/AdminView.vue"),
    },
    { path: "/:pathMatch(.*)*", redirect: "/chat" },
  ],
});

const guard = createAdminGuard(useAuthStore(pinia));
router.beforeEach((to) => guard({ fullPath: to.fullPath, meta: to.meta }));
