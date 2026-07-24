<script setup lang="ts">
import { Delete, RefreshRight } from "@element-plus/icons-vue";
import { ElMessageBox } from "element-plus";

import type { KnowledgeDomain, KnowledgeSource } from "@/types/api";
import { checkpointForSource, sourceEnvironmentPresentation } from "@/utils/sources";

const props = defineProps<{
  sources: KnowledgeSource[];
  domains: KnowledgeDomain[];
  loading: boolean;
  error: string;
  actionLoading: boolean;
}>();

const emit = defineEmits<{
  sync: [source: KnowledgeSource];
  delete: [source: KnowledgeSource, confirmName: string];
}>();

const typeLabels = { git: "Git", document: "文档", swagger: "Swagger" } as const;

function domainLabel(source: KnowledgeSource): string {
  if (source.source_type === "git") return "按路径规则分流";
  return props.domains.find((domain) => domain.id === source.domain_id)?.name ?? source.domain_id ?? "-";
}

function statusFor(source: KnowledgeSource) {
  if (source.config.lifecycle_state === "deleting") return { label: "删除中", type: "warning" };
  if (!source.enabled) return { label: "已停用", type: "info" };
  return { label: "已启用", type: "success" };
}

function environmentFor(source: KnowledgeSource) {
  return sourceEnvironmentPresentation(source);
}

async function confirmDelete(source: KnowledgeSource) {
  try {
    const result = await ElMessageBox.prompt(
      `请输入知识源名称“${source.name}”以确认删除。`,
      "删除知识源",
      {
        confirmButtonText: "删除",
        cancelButtonText: "取消",
        confirmButtonClass: "el-button--danger",
        inputPlaceholder: source.name,
        inputValidator: (value) => value === source.name || "名称必须完全一致",
      },
    );
    emit("delete", source, result.value);
  } catch {
    // Closing the confirmation leaves the source unchanged.
  }
}
</script>

<template>
  <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" />
  <div v-else class="table-shell" v-loading="loading">
    <el-empty v-if="!loading && sources.length === 0" description="暂无知识源" />
    <el-table v-else :data="sources" row-key="id" table-layout="fixed">
      <el-table-column label="名称" min-width="230">
        <template #default="{ row }">
          <div class="source-identity">
            <div class="source-identity__title">
              <span>{{ row.name }}</span>
              <el-tag
                v-if="environmentFor(row)"
                size="small"
                effect="plain"
                :type="environmentFor(row)?.branch === 'develop' ? 'warning' : 'info'"
              >{{ environmentFor(row)?.branch }}</el-tag>
            </div>
            <small v-if="environmentFor(row)">{{ environmentFor(row)?.note }}</small>
          </div>
        </template>
      </el-table-column>
      <el-table-column label="类型" width="104">
        <template #default="{ row }">
          <el-tag effect="plain" type="info">{{ typeLabels[row.source_type as KnowledgeSource['source_type']] }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="状态" width="104">
        <template #default="{ row }">
          <el-tag :type="statusFor(row).type" effect="light">{{ statusFor(row).label }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="领域" min-width="150" show-overflow-tooltip>
        <template #default="{ row }">{{ domainLabel(row) }}</template>
      </el-table-column>
      <el-table-column label="检查点" min-width="160" show-overflow-tooltip>
        <template #default="{ row }">
          <code>{{ checkpointForSource(row) }}</code>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="112" fixed="right">
        <template #default="{ row }">
          <div class="row-actions">
            <el-tooltip v-if="row.source_type === 'git'" content="立即同步" placement="top">
              <el-button
                :icon="RefreshRight"
                circle
                aria-label="立即同步"
                :loading="actionLoading"
                :disabled="!row.enabled"
                @click="emit('sync', row)"
              />
            </el-tooltip>
            <el-tooltip content="删除知识源" placement="top">
              <el-button
                :icon="Delete"
                circle
                type="danger"
                plain
                aria-label="删除知识源"
                :disabled="actionLoading || row.config.lifecycle_state === 'deleting'"
                @click="confirmDelete(row)"
              />
            </el-tooltip>
          </div>
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

.row-actions {
  display: flex;
  gap: 8px;
}

.source-identity {
  display: grid;
  min-width: 0;
  gap: 3px;
}

.source-identity__title {
  display: flex;
  align-items: center;
  min-width: 0;
  gap: 8px;
}

.source-identity__title span {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.source-identity small {
  color: var(--el-text-color-secondary);
  font-size: 11px;
  line-height: 1.3;
}

code {
  color: var(--el-text-color-regular);
  font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
  font-size: 12px;
}
</style>
