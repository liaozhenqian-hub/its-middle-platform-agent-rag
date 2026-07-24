<script setup lang="ts">
import { Delete, VideoPlay } from "@element-plus/icons-vue";

import type { EvalCase, EvalRun } from "@/types/api";

defineProps<{ cases: EvalCase[]; runs: EvalRun[]; loading: boolean }>();
const emit = defineEmits<{ run: [caseIds: string[]]; delete: [item: EvalCase]; selectRun: [run: EvalRun]; approve: [item: EvalCase, state: "approved" | "rejected"] }>();
</script>

<template>
  <div v-loading="loading" class="eval-workspace">
    <div class="eval-toolbar"><el-button type="primary" :icon="VideoPlay" :disabled="!cases.length" @click="emit('run', cases.filter((item) => item.enabled).map((item) => item.id))">运行全部已启用用例</el-button></div>
    <el-empty v-if="!cases.length" description="暂无评测用例" />
    <article v-for="item in cases" :key="item.id" class="eval-row">
      <div><strong>{{ item.name }}</strong><p>{{ item.question }}</p><el-tag size="small" effect="plain">v{{ item.version }}</el-tag><el-tag size="small" effect="plain">{{ item.suite }}</el-tag><el-tag v-for="tag in item.tags" :key="tag" size="small" effect="plain">{{ tag }}</el-tag></div>
      <div class="eval-row__actions"><el-button v-if="item.approval_state === 'candidate'" size="small" type="success" @click="emit('approve', item, 'approved')">批准</el-button><el-button v-if="item.approval_state === 'candidate'" size="small" @click="emit('approve', item, 'rejected')">驳回</el-button><el-button :icon="VideoPlay" circle @click="emit('run', [item.id])" /><el-button :icon="Delete" circle type="danger" plain @click="emit('delete', item)" /></div>
    </article>
    <div v-if="runs.length" class="run-list"><h3>最近运行</h3><button v-for="run in runs" :key="run.id" type="button" @click="emit('selectRun', run)"><span>{{ run.application_version }} · {{ run.model_name }}</span><strong>{{ run.status }} · {{ run.current_case }}/{{ run.total_cases }}</strong></button></div>
  </div>
</template>

<style scoped>
.eval-toolbar { display: flex; justify-content: flex-end; margin-bottom: 16px; }
.eval-row { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 16px; padding: 15px 0; border-bottom: 1px solid var(--el-border-color-lighter); }
.eval-row strong { font-size: 14px; }
.eval-row p { margin: 6px 0 8px; color: var(--el-text-color-regular); font-size: 13px; }
.eval-row .el-tag { margin-right: 5px; }
.eval-row__actions { display: flex; align-items: center; }
.run-list { margin-top: 24px; padding-top: 18px; border-top: 1px solid var(--el-border-color-lighter); }
.run-list h3 { margin: 0 0 10px; font-size: 14px; }
.run-list button { display: flex; justify-content: space-between; width: 100%; padding: 10px 0; color: var(--el-text-color-regular); background: transparent; border: 0; border-bottom: 1px solid var(--el-border-color-extra-light); cursor: pointer; }
@media (max-width: 640px) { .eval-row { grid-template-columns: 1fr; } .eval-row__actions { justify-content: flex-end; } }
</style>
