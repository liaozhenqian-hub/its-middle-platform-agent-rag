<script setup lang="ts">
import { ArrowLeft, Check, Close, Delete, Refresh } from "@element-plus/icons-vue";
import { onMounted } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";

import { useUserMemoryStore } from "@/stores/userMemory";
import IdentityHeader from "@/components/identity/IdentityHeader.vue";

const memory = useUserMemoryStore();
const typeLabels: Record<string, string> = {
  user_preference: "用户偏好",
  user_context: "用户上下文",
  episodic_memory: "事件记忆",
  decision_memory: "确认决策",
  procedural_memory: "排障流程",
};

onMounted(() => memory.load());

async function forget(id: string) {
  try {
    await ElMessageBox.confirm(
      "遗忘后，这条内容将立即停止参与后续回答。",
      "遗忘这条记忆",
      { type: "warning", confirmButtonText: "遗忘", cancelButtonText: "取消" },
    );
    await memory.forget(id);
    ElMessage.success("已遗忘");
  } catch {
    // Cancellation leaves the memory unchanged.
  }
}

async function confirm(id: string) {
  try {
    await memory.confirm(id);
    ElMessage.success("已加入长期记忆");
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : "确认失败");
  }
}

async function reject(id: string) {
  try {
    await memory.reject(id);
    ElMessage.success("候选记忆已驳回");
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : "驳回失败");
  }
}
</script>

<template>
  <main class="memory-page">
    <header>
      <router-link to="/chat"><el-button :icon="ArrowLeft" circle aria-label="返回问答" /></router-link>
      <div><h1>我的记忆</h1><p>这里保存可跨会话复用的偏好和决策，不是聊天记录。</p></div>
      <IdentityHeader class="memory-identity" />
      <el-button :icon="Refresh" :loading="memory.loading" @click="memory.load">刷新</el-button>
    </header>
    <el-alert v-if="memory.error" :title="memory.error" type="error" :closable="false" show-icon />
    <el-alert title="历史聊天请前往“历史会话”；待确认的个人记忆将在 24 小时后自动确认，确认后仍可随时遗忘。" type="info" :closable="false" show-icon />
    <section v-loading="memory.loading" aria-live="polite">
      <div class="section-heading">
        <div><h2>24 小时后自动确认</h2><p>仅包含个人偏好和普通上下文；你可以提前确认。</p></div>
        <el-tag effect="plain">{{ memory.autoConfirmCandidates.length }}</el-tag>
      </div>
      <el-empty v-if="!memory.loading && !memory.autoConfirmCandidates.length" description="暂无自动确认候选" />
      <article v-for="item in memory.autoConfirmCandidates" :key="item.id">
        <div class="memory-copy">
          <div class="memory-meta">
            <el-tag type="warning" effect="plain">待确认</el-tag>
            <el-tag effect="plain">{{ typeLabels[item.memory_type] || item.memory_type }}</el-tag>
            <span>{{ item.domain_id || '全空间' }}</span>
          </div>
          <h3>{{ item.subject }}</h3>
          <p>{{ item.summary }}</p>
          <small v-if="item.auto_confirm_at">预计自动确认：{{ new Date(item.auto_confirm_at).toLocaleString('zh-CN') }}</small>
        </div>
        <div class="candidate-actions">
          <el-tooltip content="确认并用于后续回答" placement="top">
            <el-button :icon="Check" circle type="success" :loading="memory.confirming === item.id" aria-label="确认记忆" @click="confirm(item.id)" />
          </el-tooltip>
          <el-tooltip content="驳回，不保存这条记忆" placement="top">
            <el-button :icon="Close" circle type="danger" plain :loading="memory.rejecting === item.id" aria-label="驳回记忆" @click="reject(item.id)" />
          </el-tooltip>
        </div>
      </article>

      <div class="section-heading confirmed-heading">
        <div><h2>需要你确认</h2><p>决策、Bug 事件和排障流程不会自动生效。</p></div>
        <el-tag type="warning" effect="plain">{{ memory.explicitReviewCandidates.length }}</el-tag>
      </div>
      <el-empty v-if="!memory.loading && !memory.explicitReviewCandidates.length" description="暂无需要确认的记忆" />
      <article v-for="item in memory.explicitReviewCandidates" :key="item.id">
        <div class="memory-copy">
          <div class="memory-meta">
            <el-tag type="warning" effect="plain">需要确认</el-tag>
            <el-tag effect="plain">{{ typeLabels[item.memory_type] || item.memory_type }}</el-tag>
            <span>{{ item.domain_id || '全空间' }}</span>
          </div>
          <h3>{{ item.subject }}</h3><p>{{ item.summary }}</p>
        </div>
        <div class="candidate-actions">
          <el-tooltip content="确认并用于后续回答"><el-button :icon="Check" circle type="success" :loading="memory.confirming === item.id" @click="confirm(item.id)" /></el-tooltip>
          <el-tooltip content="驳回"><el-button :icon="Close" circle type="danger" plain :loading="memory.rejecting === item.id" @click="reject(item.id)" /></el-tooltip>
        </div>
      </article>

      <div class="section-heading confirmed-heading">
        <div><h2>已确认</h2><p>这些内容可以在相关问题中被助手召回。</p></div>
        <el-tag type="success" effect="plain">{{ memory.confirmedFacts.length }}</el-tag>
      </div>
      <el-empty v-if="!memory.loading && !memory.confirmedFacts.length" description="暂无已确认记忆" />
      <article v-for="item in memory.confirmedFacts" :key="item.id">
        <div class="memory-copy">
          <div class="memory-meta">
            <el-tag effect="plain">{{ typeLabels[item.memory_type] || item.memory_type }}</el-tag>
            <span>{{ item.domain_id || '全空间' }}</span>
          </div>
          <h3>{{ item.subject }}</h3>
          <p>{{ item.summary }}</p>
        </div>
        <el-tooltip content="遗忘" placement="top">
          <el-button :icon="Delete" circle type="danger" plain :loading="memory.forgetting === item.id" :aria-label="`遗忘 ${item.subject}`" @click="forget(item.id)" />
        </el-tooltip>
      </article>

      <div class="section-heading confirmed-heading">
        <div><h2>已确认排障流程</h2><p>只保存有证据支持的步骤模板，不保存模型思考过程或原始工具输出。</p></div>
        <el-tag type="info" effect="plain">{{ memory.confirmedProcedures.length }}</el-tag>
      </div>
      <el-empty
        v-if="!memory.loading && !memory.confirmedProcedures.length"
        description="暂无排障流程记忆"
      />
      <article v-for="item in memory.confirmedProcedures" :key="item.id">
        <div class="memory-copy">
          <div class="memory-meta">
            <el-tag type="success" effect="plain">已确认</el-tag>
            <el-tag effect="plain">排障流程</el-tag>
            <span>{{ item.domain_id || '全空间' }}</span>
          </div>
          <h3>{{ item.subject }}</h3>
          <p>{{ item.summary }}</p>
        </div>
        <el-tooltip content="遗忘" placement="top">
          <el-button :icon="Delete" circle type="danger" plain :loading="memory.forgetting === item.id" :aria-label="`遗忘 ${item.subject}`" @click="forget(item.id)" />
        </el-tooltip>
      </article>
    </section>
  </main>
</template>

<style scoped>
.memory-page { width: min(920px, calc(100% - 32px)); min-height: 100vh; margin: 0 auto; padding: 24px 0 48px; }
header { display: grid; grid-template-columns: 40px minmax(0, 1fr) auto auto; align-items: start; gap: 14px; padding-bottom: 20px; border-bottom: 1px solid var(--el-border-color-lighter); }
h1, h2, h3, p { margin: 0; }
h1 { font-size: 22px; }
header p { margin-top: 4px; color: var(--el-text-color-secondary); font-size: 13px; }
section { min-height: 180px; }
.section-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; padding: 22px 4px 10px; }
.section-heading h2 { font-size: 17px; }
.section-heading p { margin-top: 4px; color: var(--el-text-color-secondary); font-size: 13px; }
.confirmed-heading { margin-top: 12px; border-top: 1px solid var(--el-border-color-lighter); }
article { display: flex; align-items: flex-start; justify-content: space-between; gap: 20px; padding: 20px 4px; border-bottom: 1px solid var(--el-border-color-lighter); }
.memory-copy { min-width: 0; }
.memory-meta { display: flex; align-items: center; gap: 10px; color: var(--el-text-color-secondary); font-size: 12px; }
h3 { margin-top: 10px; font-size: 15px; }
article p { margin-top: 6px; color: var(--el-text-color-regular); font-size: 14px; line-height: 1.65; overflow-wrap: anywhere; }
article small { display: block; margin-top: 6px; color: var(--el-text-color-secondary); font-size: 11px; }
.candidate-actions { display: flex; flex: 0 0 auto; gap: 8px; }
.el-alert { margin-top: 18px; }
@media (max-width: 640px) {
  .memory-page { width: calc(100% - 20px); padding-top: 14px; }
  header { grid-template-columns: 36px minmax(0, 1fr); }
  .memory-identity, header > .el-button { grid-column: 2; justify-self: end; }
}
</style>
