<script setup lang="ts">
import { ArrowLeft, CopyDocument, Delete, Key, Plus } from "@element-plus/icons-vue";
import { onMounted, ref } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";

import { useUserIdentityStore } from "@/stores/userIdentity";

const identity = useUserIdentityStore();
const createOpen = ref(false);
const createdOpen = ref(false);
const creating = ref(false);
const name = ref("");
const scopes = ref(["agent:query", "memory:read"]);

const scopeLabels: Record<string, string> = {
  "agent:query": "问答查询",
  "memory:read": "读取个人记忆",
};

onMounted(async () => {
  await identity.load();
  if (identity.authenticated) await identity.loadTokens();
});

async function createToken() {
  if (!name.value.trim() || !scopes.value.length) return;
  creating.value = true;
  try {
    await identity.createToken(name.value.trim(), scopes.value);
    createOpen.value = false;
    createdOpen.value = true;
    name.value = "";
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : "创建失败");
  } finally {
    creating.value = false;
  }
}

async function copyCreatedToken() {
  await navigator.clipboard.writeText(identity.createdToken);
  ElMessage.success("Token 已复制");
}

function closeCreated() {
  createdOpen.value = false;
  identity.clearCreatedToken();
}

async function revoke(id: string, tokenName: string) {
  try {
    await ElMessageBox.confirm(
      `撤销后，使用“${tokenName}”的 Codex 将立即无法访问。`,
      "撤销 Token",
      { type: "warning", confirmButtonText: "撤销", cancelButtonText: "取消" },
    );
    await identity.revokeToken(id);
    ElMessage.success("Token 已撤销");
  } catch {
    // User cancellation leaves the token active.
  }
}
</script>

<template>
  <main class="account-page">
    <header>
      <router-link to="/chat">
        <el-button :icon="ArrowLeft" circle aria-label="返回问答" />
      </router-link>
      <div class="heading">
        <h1>个人 Token</h1>
        <p>用于 Codex 以你的飞书身份访问问答和个人记忆。</p>
      </div>
      <el-button
        v-if="identity.authenticated"
        type="primary"
        :icon="Plus"
        @click="createOpen = true"
      >新建 Token</el-button>
    </header>

    <el-alert
      v-if="!identity.loading && !identity.authenticated"
      title="请先使用飞书登录，才能创建个人 Token。"
      type="info"
      :closable="false"
      show-icon
    >
      <el-button type="primary" @click="identity.login">飞书登录</el-button>
    </el-alert>

    <section v-else v-loading="identity.loading" class="token-list">
      <el-empty v-if="!identity.loading && !identity.tokens.length" description="暂无个人 Token" />
      <article v-for="token in identity.tokens" :key="token.id">
        <span class="token-icon"><Key /></span>
        <div class="token-copy">
          <div class="token-title">
            <h2>{{ token.name }}</h2>
            <el-tag v-if="token.revoked_at" type="info" effect="plain">已撤销</el-tag>
            <el-tag v-else type="success" effect="plain">可用</el-tag>
          </div>
          <code>{{ token.display_prefix }}…</code>
          <p>{{ token.scopes.map((scope) => scopeLabels[scope] || scope).join(" · ") }}</p>
          <small>最后使用：{{ token.last_used_at ? new Date(token.last_used_at).toLocaleString() : "尚未使用" }}</small>
        </div>
        <el-tooltip content="撤销" placement="top">
          <el-button
            :icon="Delete"
            circle
            plain
            type="danger"
            :disabled="Boolean(token.revoked_at)"
            :aria-label="`撤销 ${token.name}`"
            @click="revoke(token.id, token.name)"
          />
        </el-tooltip>
      </article>
    </section>

    <el-dialog v-model="createOpen" title="新建个人 Token" width="min(480px, calc(100vw - 28px))">
      <el-form label-position="top" @submit.prevent="createToken">
        <el-form-item label="名称">
          <el-input v-model="name" maxlength="100" placeholder="例如：办公电脑 Codex" />
        </el-form-item>
        <el-form-item label="权限">
          <el-checkbox-group v-model="scopes">
            <el-checkbox value="agent:query">问答查询</el-checkbox>
            <el-checkbox value="memory:read">读取个人记忆</el-checkbox>
          </el-checkbox-group>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createOpen = false">取消</el-button>
        <el-button type="primary" :loading="creating" :disabled="!name.trim() || !scopes.length" @click="createToken">创建</el-button>
      </template>
    </el-dialog>

    <el-dialog
      v-model="createdOpen"
      title="Token 已创建"
      width="min(560px, calc(100vw - 28px))"
      :close-on-click-modal="false"
      @closed="closeCreated"
    >
      <el-alert title="完整 Token 只显示这一次，请立即存入 Codex 的安全配置。" type="warning" :closable="false" show-icon />
      <div class="created-token">
        <code>{{ identity.createdToken }}</code>
        <el-button :icon="CopyDocument" circle aria-label="复制 Token" @click="copyCreatedToken" />
      </div>
      <template #footer><el-button type="primary" @click="closeCreated">我已保存</el-button></template>
    </el-dialog>
  </main>
</template>

<style scoped>
.account-page { width: min(920px, calc(100% - 32px)); min-height: 100vh; margin: 0 auto; padding: 24px 0 48px; }
header { display: grid; grid-template-columns: 40px minmax(0, 1fr) auto; align-items: start; gap: 14px; padding-bottom: 20px; border-bottom: 1px solid var(--el-border-color-lighter); }
h1, h2, p { margin: 0; }
h1 { font-size: 22px; }
.heading p { margin-top: 4px; color: var(--el-text-color-secondary); font-size: 13px; }
.token-list { min-height: 180px; }
article { display: grid; grid-template-columns: 38px minmax(0, 1fr) 40px; gap: 14px; align-items: start; padding: 20px 4px; border-bottom: 1px solid var(--el-border-color-lighter); }
.token-icon { display: grid; place-items: center; width: 36px; height: 36px; border-radius: 6px; background: var(--el-fill-color-light); color: var(--el-text-color-secondary); }
.token-icon svg { width: 18px; }
.token-title { display: flex; align-items: center; gap: 8px; }
h2 { font-size: 15px; }
.token-copy code { display: block; margin-top: 8px; font-size: 13px; }
.token-copy p, .token-copy small { display: block; margin-top: 6px; color: var(--el-text-color-secondary); font-size: 12px; }
.created-token { display: grid; grid-template-columns: minmax(0, 1fr) 40px; gap: 10px; align-items: center; margin-top: 16px; padding: 12px; background: var(--el-fill-color-light); border-radius: 6px; }
.created-token code { overflow-wrap: anywhere; user-select: all; }
.el-alert { margin-top: 18px; }
@media (max-width: 640px) {
  .account-page { width: calc(100% - 20px); padding-top: 14px; }
  header { grid-template-columns: 36px minmax(0, 1fr); }
  header > .el-button { grid-column: 2; justify-self: start; }
}
</style>
