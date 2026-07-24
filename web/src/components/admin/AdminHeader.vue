<script setup lang="ts">
import { SwitchButton } from "@element-plus/icons-vue";

import { useAuthStore } from "@/stores/auth";

const auth = useAuthStore();

async function logout() {
  await auth.logout();
}
</script>

<template>
  <header class="admin-header">
    <div>
      <p class="admin-header__eyebrow">中台知识系统</p>
      <h1>管理工作台</h1>
    </div>
    <div class="admin-header__account">
      <span>{{ auth.identity?.username }}</span>
      <el-tooltip content="退出登录" placement="bottom">
        <el-button
          :icon="SwitchButton"
          circle
          aria-label="退出登录"
          data-testid="logout"
          :loading="auth.loading"
          @click="logout"
        />
      </el-tooltip>
    </div>
  </header>
</template>

<style scoped>
.admin-header {
  display: flex;
  min-height: 68px;
  align-items: center;
  justify-content: space-between;
  gap: 24px;
  border-bottom: 1px solid var(--el-border-color-lighter);
  background: var(--el-bg-color);
  padding: 12px clamp(16px, 4vw, 48px);
}

.admin-header h1,
.admin-header p {
  margin: 0;
}

.admin-header h1 {
  color: var(--el-text-color-primary);
  font-size: 22px;
  line-height: 1.3;
}

.admin-header__eyebrow {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}

.admin-header__account {
  display: flex;
  align-items: center;
  gap: 12px;
  color: var(--el-text-color-regular);
  font-size: 14px;
}
</style>
