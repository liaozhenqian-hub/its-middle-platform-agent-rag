<script setup lang="ts">
import { Key, Promotion, SwitchButton, UserFilled } from "@element-plus/icons-vue";
import { computed, onMounted } from "vue";
import { ElMessage } from "element-plus";

import { useUserIdentityStore } from "@/stores/userIdentity";

const identity = useUserIdentityStore();
const mergeSummary = computed(() => {
  const preview = identity.mergePreview;
  if (!preview?.available) return "";
  const items = [
    preview.conversations ? `${preview.conversations} 个对话` : "",
    preview.memories ? `${preview.memories} 条记忆` : "",
  ].filter(Boolean);
  return items.join("、") || "当前设备数据";
});

onMounted(() => {
  if (!identity.identity && !identity.loading) void identity.load();
});

async function merge(confirm: boolean) {
  try {
    await identity.mergeAnonymous(confirm);
    ElMessage.success(confirm ? "设备数据已合并" : "已保留设备数据");
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : "操作失败");
  }
}

async function logout() {
  try {
    await identity.logout();
    ElMessage.success("已退出飞书账号");
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : "退出失败");
  }
}
</script>

<template>
  <div class="identity-shell" aria-label="当前身份">
    <div v-if="identity.mergePreview?.available" class="merge-prompt">
      <span>合并当前设备数据<span v-if="mergeSummary">（{{ mergeSummary }}）</span></span>
      <el-button size="small" type="primary" @click="merge(true)">合并</el-button>
      <el-button size="small" text @click="merge(false)">稍后处理</el-button>
    </div>
    <div class="identity-control">
      <template v-if="identity.authenticated">
        <router-link to="/account" class="identity-link">
          <el-icon><UserFilled /></el-icon>
          <span>{{ identity.identity?.display_name }}</span>
        </router-link>
        <router-link to="/account">
          <el-tooltip content="个人 Token" placement="bottom">
            <el-button :icon="Key" circle aria-label="个人 Token" />
          </el-tooltip>
        </router-link>
        <el-tooltip content="退出飞书" placement="bottom">
          <el-button :icon="SwitchButton" circle aria-label="退出飞书" @click="logout" />
        </el-tooltip>
      </template>
      <template v-else>
        <span class="device-label">
          <el-icon><UserFilled /></el-icon>
          {{ identity.identity?.display_name || "当前设备" }}
        </span>
        <el-button
          v-if="identity.identity?.feishu_login_available"
          :icon="Promotion"
          plain
          @click="identity.login"
        >飞书登录</el-button>
        <el-tooltip v-else content="需要配置企业 tenant key 并启用 OAuth" placement="bottom">
          <el-button :icon="Promotion" plain disabled>飞书登录未配置</el-button>
        </el-tooltip>
      </template>
    </div>
  </div>
</template>

<style scoped>
.identity-shell { display: flex; align-items: center; gap: 10px; min-width: 0; }
.identity-control { display: flex; align-items: center; gap: 8px; min-width: 0; }
.identity-link, .device-label { display: inline-flex; align-items: center; gap: 6px; color: var(--el-text-color-regular); font-size: 13px; text-decoration: none; white-space: nowrap; }
.identity-link span { max-width: 120px; overflow: hidden; text-overflow: ellipsis; }
.merge-prompt { display: flex; align-items: center; gap: 6px; padding: 5px 8px; border: 1px solid var(--el-color-warning-light-5); background: var(--el-color-warning-light-9); border-radius: 6px; color: var(--el-text-color-regular); font-size: 12px; }
@media (max-width: 760px) {
  .merge-prompt span { max-width: 150px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .device-label { display: none; }
  .identity-control :deep(.el-button span) { display: none; }
}
</style>
