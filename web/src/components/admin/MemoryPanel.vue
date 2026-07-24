<script setup lang="ts">
import { computed } from "vue";
import { Delete, Refresh } from "@element-plus/icons-vue";
import { ElMessage, ElMessageBox } from "element-plus";

import { useMemoryStore } from "@/stores/memory";

const memory = useMemoryStore();
const typeLabels: Record<string, string> = {
  user_preference: "用户偏好",
  user_context: "用户上下文",
  episodic_memory: "事件记忆",
  decision_memory: "确认决策",
  procedural_memory: "排障流程",
};

const personalCandidateCount = computed(() =>
  Object.values(memory.personalStatistics?.candidate || {}).reduce((sum, value) => sum + (value || 0), 0),
);

async function approve(id: string) {
  await memory.approve(id);
  ElMessage.success("记忆已确认，将在相关问题中参与召回");
}

async function reject(id: string) {
  await memory.reject(id);
  ElMessage.success("候选记忆已驳回");
}

async function remove(id: string) {
  try {
    await ElMessageBox.confirm(
      "删除后该记忆将立即停止参与回答，且对应记忆向量会一并删除。",
      "删除已确认记忆",
      { type: "warning", confirmButtonText: "删除", cancelButtonText: "取消" },
    );
    await memory.remove(id);
    ElMessage.success("记忆已删除");
  } catch {
    // Cancellation leaves the memory unchanged.
  }
}

async function reviewPromotion(id: string, decision: "approve" | "reject") {
  await memory.reviewPromotion(id, decision);
  ElMessage.success(decision === "approve" ? "领域记忆已批准" : "领域提升已驳回");
}
</script>

<template>
  <section class="memory-panel" aria-labelledby="memory-heading">
    <div class="memory-heading">
      <div>
        <h2 id="memory-heading">领域记忆审核</h2>
        <p>这里只审核领域共享记忆；个人记忆由用户本人管理。</p>
      </div>
      <el-button :icon="Refresh" :loading="memory.loading" @click="memory.load">刷新</el-button>
    </div>
    <el-alert v-if="memory.error" :title="memory.error" type="error" :closable="false" show-icon />
    <div class="personal-statistics">
      <span>个人记忆仅展示脱敏统计</span>
      <strong>个人候选 {{ personalCandidateCount }}</strong>
    </div>

    <h3>待提升审核 <span>{{ memory.promotions.length }}</span></h3>
    <el-table :data="memory.promotions" v-loading="memory.loading" empty-text="暂无待提升领域记忆">
      <el-table-column prop="public_summary" label="脱敏领域摘要" min-width="320" />
      <el-table-column prop="target_domain_id" label="目标领域" width="140" />
      <el-table-column prop="valid_until" label="有效期" width="190" />
      <el-table-column label="操作" width="150" fixed="right">
        <template #default="{ row }">
          <el-button link type="success" :loading="memory.actionLoading === row.id" @click="reviewPromotion(row.id, 'approve')">批准</el-button>
          <el-button link type="danger" :disabled="memory.actionLoading === row.id" @click="reviewPromotion(row.id, 'reject')">驳回</el-button>
        </template>
      </el-table-column>
    </el-table>

    <h3>待审核候选 <span>{{ memory.candidates.length }}</span></h3>
    <el-table :data="memory.candidates" v-loading="memory.loading" empty-text="暂无待审核候选">
      <el-table-column prop="summary" label="候选内容" min-width="280">
        <template #default="{ row }"><strong>{{ row.subject }}</strong><p>{{ row.summary }}</p></template>
      </el-table-column>
      <el-table-column label="领域" width="150">
        <template #default="{ row }"><el-tag effect="plain">{{ row.domain_id || '中台' }}</el-tag></template>
      </el-table-column>
      <el-table-column label="类型" width="110">
        <template #default="{ row }">{{ typeLabels[row.memory_type] || row.memory_type }}</template>
      </el-table-column>
      <el-table-column label="置信度" width="90">
        <template #default="{ row }">{{ Math.round(row.confidence * 100) }}%</template>
      </el-table-column>
      <el-table-column label="操作" width="150" fixed="right">
        <template #default="{ row }">
          <el-button link type="success" :loading="memory.actionLoading === row.id" @click="approve(row.id)">确认</el-button>
          <el-button link type="danger" :disabled="memory.actionLoading === row.id" @click="reject(row.id)">驳回</el-button>
        </template>
      </el-table-column>
    </el-table>

    <h3 class="confirmed-heading">已确认记忆 <span>{{ memory.memories.length }}</span></h3>
    <el-table :data="memory.memories" v-loading="memory.loading" empty-text="暂无已确认记忆">
      <el-table-column prop="summary" label="生效内容" min-width="320">
        <template #default="{ row }"><strong>{{ row.subject }}</strong><p>{{ row.summary }}</p></template>
      </el-table-column>
      <el-table-column label="领域" width="130">
        <template #default="{ row }">{{ row.domain_id || '全空间' }}</template>
      </el-table-column>
      <el-table-column label="操作" width="80" fixed="right">
        <template #default="{ row }">
          <el-tooltip content="删除记忆" placement="top"><el-button circle :icon="Delete" type="danger" plain @click="remove(row.id)" /></el-tooltip>
        </template>
      </el-table-column>
    </el-table>
  </section>
</template>

<style scoped>
.memory-panel { min-width: 0; }
.memory-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; margin-bottom: 20px; }
h2, h3, p { margin: 0; }
h2 { font-size: 18px; }
.memory-heading p { margin-top: 5px; color: var(--el-text-color-secondary); font-size: 13px; }
h3 { margin: 22px 0 10px; font-size: 14px; }
h3 span { color: var(--el-text-color-secondary); font-weight: 400; }
.confirmed-heading { padding-top: 6px; border-top: 1px solid var(--el-border-color-lighter); }
.personal-statistics { display: flex; justify-content: space-between; gap: 16px; margin: 14px 0; padding: 10px 12px; color: var(--el-text-color-secondary); background: var(--el-fill-color-light); border-radius: 6px; font-size: 12px; }
:deep(.el-table p) { margin-top: 4px; color: var(--el-text-color-regular); font-size: 13px; line-height: 1.5; white-space: normal; }
:deep(.el-table strong) { font-size: 13px; }
@media (max-width: 640px) {
  .memory-heading { flex-direction: column; }
  .memory-heading .el-button { align-self: flex-end; }
}
</style>
