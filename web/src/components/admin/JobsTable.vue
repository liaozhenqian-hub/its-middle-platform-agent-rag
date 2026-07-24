<script setup lang="ts">
import { RefreshRight } from "@element-plus/icons-vue";

import type { KnowledgeSource, SyncJob } from "@/types/api";

const props = defineProps<{
  jobs: SyncJob[];
  sources: KnowledgeSource[];
  loading: boolean;
  error: string;
  actionLoading: boolean;
}>();

const emit = defineEmits<{ retry: [job: SyncJob] }>();

const stateLabels = {
  queued: { label: "等待中", type: "info" },
  running: { label: "执行中", type: "warning" },
  succeeded: { label: "已完成", type: "success" },
  failed: { label: "失败", type: "danger" },
} as const;

function sourceName(sourceId: string): string {
  return props.sources.find((source) => source.id === sourceId)?.name ?? sourceId;
}

function formatTime(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN", { hour12: false });
}
</script>

<template>
  <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" />
  <div v-else class="table-shell" v-loading="loading">
    <el-empty v-if="!loading && jobs.length === 0" description="暂无同步任务" />
    <el-table v-else :data="jobs" row-key="id" table-layout="fixed">
      <el-table-column label="知识源" min-width="180" show-overflow-tooltip>
        <template #default="{ row }">{{ sourceName(row.source_id) }}</template>
      </el-table-column>
      <el-table-column prop="kind" label="任务" width="108" />
      <el-table-column label="状态" width="104">
        <template #default="{ row }">
          <el-tag :type="stateLabels[row.state as SyncJob['state']].type">
            {{ stateLabels[row.state as SyncJob['state']].label }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="attempt" label="尝试" width="72" />
      <el-table-column label="更新时间" min-width="168">
        <template #default="{ row }">{{ formatTime(row.updated_at) }}</template>
      </el-table-column>
      <el-table-column prop="error" label="结果" min-width="190" show-overflow-tooltip>
        <template #default="{ row }">{{ row.error || "-" }}</template>
      </el-table-column>
      <el-table-column label="操作" width="72" fixed="right">
        <template #default="{ row }">
          <el-tooltip v-if="row.state === 'failed'" content="重试任务" placement="top">
            <el-button
              :icon="RefreshRight"
              circle
              aria-label="重试任务"
              :loading="actionLoading"
              @click="emit('retry', row)"
            />
          </el-tooltip>
        </template>
      </el-table-column>
    </el-table>
  </div>
</template>

<style scoped>
.table-shell {
  min-height: 160px;
  overflow-x: auto;
}

.table-shell :deep(.el-table) {
  min-width: 760px;
}
</style>
