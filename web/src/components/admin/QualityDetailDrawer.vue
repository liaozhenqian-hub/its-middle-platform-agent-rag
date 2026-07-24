<script setup lang="ts">
import { computed } from "vue";

import type { QualityTurn } from "@/types/api";
import { citationDisplayName, sanitizeCitationText } from "@/utils/citations";
import { renderMarkdown } from "@/utils/markdown";

const props = defineProps<{ modelValue: boolean; turn: QualityTurn | null; loading: boolean }>();
const emit = defineEmits<{ "update:modelValue": [value: boolean] }>();
const open = computed({ get: () => props.modelValue, set: (value) => emit("update:modelValue", value) });
const domainLabels: Record<string, string> = {
  "metric-platform": "指标平台",
  "approval-flow": "审批流",
  workflow: "工作流",
  bug: "Bug 分析",
};
const specialistLabels: Record<string, string> = {
  metric_platform_expert: "指标平台专家",
  approval_flow_expert: "审批流专家",
  workflow_expert: "工作流专家",
  bug_diagnosis_expert: "Bug 分析专家",
};
const specialists = computed(() => {
  const labels = (props.turn?.tools ?? [])
    .map((tool) => specialistLabels[tool.tool_name])
    .filter((label): label is string => Boolean(label));
  return [...new Set(labels)];
});
const publicAnswer = computed(() => sanitizeCitationText(
  props.turn?.answer || "未生成回答",
  props.turn?.citations || [],
));
</script>

<template>
  <el-drawer v-model="open" title="问答详情" size="min(720px, 92%)">
    <div v-loading="loading" class="quality-detail">
      <template v-if="turn">
        <dl>
          <div><dt>渠道</dt><dd>{{ turn.channel }}</dd></div>
          <div><dt>用户</dt><dd>{{ turn.user_name || turn.user_id || "匿名" }}</dd></div>
          <div><dt>状态</dt><dd>{{ turn.status }}</dd></div>
          <div><dt>模型</dt><dd>{{ turn.provider }} / {{ turn.model_name }}</dd></div>
          <div><dt>路由领域</dt><dd>{{ domainLabels[turn.domain_id || ""] || "中台 / 跨领域" }}</dd></div>
          <div><dt>调用专家</dt><dd>{{ specialists.join(" + ") || "未调用专家" }}</dd></div>
          <div><dt>最终回答者</dt><dd>{{ turn.last_agent || "-" }}</dd></div>
          <div><dt>耗时</dt><dd>{{ turn.duration_ms ? `${Math.round(turn.duration_ms)} ms` : "-" }}</dd></div>
        </dl>
        <section><h3>用户问题</h3><p class="raw-text">{{ turn.question }}</p></section>
        <section><h3>最终回答</h3><div class="answer" v-html="renderMarkdown(publicAnswer)" /></section>
        <section><h3>工具路线</h3><el-tag v-for="tool in turn.tools" :key="tool.tool_call_id" effect="plain">{{ tool.tool_name }} · {{ tool.status }}</el-tag><el-empty v-if="!turn.tools.length" description="未调用工具" :image-size="48" /></section>
        <section><h3>引用</h3><p v-for="citation in turn.citations" :key="`${citation.source_type}:${citation.source_id}`">{{ citationDisplayName(citation) }}</p><el-empty v-if="!turn.citations.length" description="无引用" :image-size="48" /></section>
        <section><h3>用户反馈</h3><p v-for="item in turn.feedback" :key="item.id">{{ item.rating === 'positive' ? '赞同' : '负面' }} · {{ item.user_name || item.user_id || '网页用户' }}<span v-if="item.reason"> · {{ item.reason }}</span></p><el-empty v-if="!turn.feedback.length" description="暂无反馈" :image-size="48" /></section>
      </template>
    </div>
  </el-drawer>
</template>

<style scoped>
.quality-detail { min-height: 240px; }
dl { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px 20px; margin: 0 0 24px; }
dl div { min-width: 0; }
dt { color: var(--el-text-color-secondary); font-size: 11px; }
dd { margin: 3px 0 0; color: var(--el-text-color-primary); font-size: 13px; overflow-wrap: anywhere; }
section { padding: 18px 0; border-top: 1px solid var(--el-border-color-lighter); }
h3 { margin: 0 0 10px; font-size: 14px; }
p { margin: 6px 0; color: var(--el-text-color-regular); font-size: 13px; line-height: 1.6; overflow-wrap: anywhere; }
.raw-text { white-space: pre-wrap; }
.answer { font-size: 13px; line-height: 1.7; }
.el-tag { margin: 0 6px 6px 0; }
@media (max-width: 540px) { dl { grid-template-columns: 1fr; } }
</style>
