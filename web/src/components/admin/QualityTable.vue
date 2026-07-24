<script setup lang="ts">
import { Delete, DocumentAdd, View } from "@element-plus/icons-vue";

import type { QualityTurn } from "@/types/api";

defineProps<{ turns: QualityTurn[]; loading: boolean }>();
const emit = defineEmits<{
  select: [turn: QualityTurn];
  delete: [turn: QualityTurn];
  promote: [turn: QualityTurn];
}>();

const channelLabels: Record<string, string> = { web: "网页", api: "API", feishu: "飞书" };
const statusLabels: Record<string, string> = {
  completed: "完成",
  clarification_required: "待补充",
  no_answer: "无答案",
  error: "失败",
  timeout: "超时",
  cancelled: "取消",
  interrupted: "中断",
  running: "执行中",
};
const domainLabels: Record<string, string> = {
  "metric-platform": "指标平台",
  "approval-flow": "审批流",
  workflow: "工作流",
};
</script>

<template>
  <div v-loading="loading" class="quality-table">
    <el-empty v-if="!loading && !turns.length" description="暂无问答记录" />
    <article v-for="turn in turns" :key="turn.id" class="quality-row">
      <div class="quality-row__main">
        <div class="quality-row__meta">
          <el-tag size="small" effect="plain">{{ channelLabels[turn.channel] || turn.channel }}</el-tag>
          <span>{{ statusLabels[turn.status] || turn.status }}</span>
          <span>{{ domainLabels[turn.domain_id || ""] || "中台 / 跨领域" }}</span>
          <span>{{ turn.user_name || turn.user_id || "匿名网页用户" }}</span>
          <time>{{ new Date(turn.created_at).toLocaleString() }}</time>
        </div>
        <strong>{{ turn.question }}</strong>
        <p>{{ turn.answer || turn.error_type || "尚未生成回答" }}</p>
        <div v-if="turn.feedback?.length" class="quality-row__feedback">
          {{ turn.feedback.filter((item) => item.rating === "positive").length }} 个赞同
          · {{ turn.feedback.filter((item) => item.rating === "negative").length }} 个负面反馈
        </div>
      </div>
      <div class="quality-row__actions">
        <el-tooltip content="查看详情"><el-button :icon="View" circle @click="emit('select', turn)" /></el-tooltip>
        <el-tooltip content="加入评测集"><el-button :icon="DocumentAdd" circle @click="emit('promote', turn)" /></el-tooltip>
        <el-tooltip content="删除记录"><el-button :icon="Delete" circle type="danger" plain @click="emit('delete', turn)" /></el-tooltip>
      </div>
    </article>
  </div>
</template>

<style scoped>
.quality-table { min-height: 180px; }
.quality-row { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 18px; padding: 16px 0; border-bottom: 1px solid var(--el-border-color-lighter); }
.quality-row:first-child { padding-top: 0; }
.quality-row:last-child { border-bottom: 0; }
.quality-row__main { min-width: 0; }
.quality-row__main strong { display: block; margin: 8px 0 5px; color: var(--el-text-color-primary); font-size: 14px; overflow-wrap: anywhere; }
.quality-row__main p { display: -webkit-box; margin: 0; color: var(--el-text-color-regular); font-size: 13px; line-height: 1.6; overflow: hidden; -webkit-box-orient: vertical; -webkit-line-clamp: 2; }
.quality-row__meta { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; color: var(--el-text-color-secondary); font-size: 11px; }
.quality-row__feedback { margin-top: 7px; color: var(--el-text-color-secondary); font-size: 11px; }
.quality-row__actions { display: flex; align-items: center; gap: 6px; }
@media (max-width: 640px) { .quality-row { grid-template-columns: 1fr; } .quality-row__actions { justify-content: flex-end; } }
</style>
