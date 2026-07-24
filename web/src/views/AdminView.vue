<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref } from "vue";
import { Download, Plus, Refresh, Search } from "@element-plus/icons-vue";
import { ElMessage, ElMessageBox } from "element-plus";

import AddSourceDialog from "@/components/admin/AddSourceDialog.vue";
import AdminHeader from "@/components/admin/AdminHeader.vue";
import JobsTable from "@/components/admin/JobsTable.vue";
import EvalCasesTable from "@/components/admin/EvalCasesTable.vue";
import QualityDetailDrawer from "@/components/admin/QualityDetailDrawer.vue";
import QualityTable from "@/components/admin/QualityTable.vue";
import MemoryPanel from "@/components/admin/MemoryPanel.vue";
import SourceTable from "@/components/admin/SourceTable.vue";
import { useAdminStore } from "@/stores/admin";
import { useQualityStore } from "@/stores/quality";
import { useMemoryStore } from "@/stores/memory";
import type { EvalCase, EvalRun, KnowledgeSource, QualityTurn, SyncJob } from "@/types/api";

const admin = useAdminStore();
const quality = useQualityStore();
const memory = useMemoryStore();
const addDialogOpen = ref(false);
const activeTab = ref("sources");
const detailOpen = ref(false);
const evalDialogOpen = ref(false);
const promoteTarget = ref<QualityTurn | null>(null);
const runDrawerOpen = ref(false);
let evalPoll: ReturnType<typeof setInterval> | null = null;
const evalForm = reactive({
  name: "",
  requiredTools: "",
  citationTypes: "",
  requiredFacts: "",
  forbiddenFacts: "",
  tags: "",
});

const exportUrl = computed(() => {
  const query = new URLSearchParams({ format: "jsonl" });
  if (quality.filters.channel) query.set("channel", quality.filters.channel);
  if (quality.filters.status) query.set("status", quality.filters.status);
  if (quality.filters.rating) query.set("rating", quality.filters.rating);
  if (quality.filters.query.trim()) query.set("query", quality.filters.query.trim());
  return `/api/v1/admin/quality/export?${query}`;
});

onMounted(async () => {
  await admin.loadDashboard();
  await Promise.all([
    quality.loadTurns(), quality.loadAnalytics(), quality.loadAnnotations(),
    quality.loadEvalCases(), quality.loadEvalRuns(), memory.load(),
  ]);
  admin.startJobPolling(3_000);
});

onBeforeUnmount(() => {
  admin.stopJobPolling();
  if (evalPoll) clearInterval(evalPoll);
});

async function refreshQuality() {
  await Promise.all([
    quality.loadTurns(1), quality.loadAnalytics(), quality.loadAnnotations(),
  ]);
}

function pollRun(runId: string) {
  if (evalPoll) clearInterval(evalPoll);
  evalPoll = setInterval(async () => {
    await quality.loadEvalRunDetail(runId);
    const status = quality.selectedRun?.status;
    if (status && !["queued", "running"].includes(status) && evalPoll) {
      clearInterval(evalPoll);
      evalPoll = null;
      await quality.loadEvalRuns();
    }
  }, 3_000);
}

async function sync(source: KnowledgeSource) {
  try {
    await admin.syncSource(source);
    ElMessage.success("同步任务已创建");
  } catch {
    ElMessage.error(admin.actionError || "无法创建同步任务");
  }
}

async function remove(source: KnowledgeSource, confirmName: string) {
  try {
    await admin.deleteSource(source, confirmName);
    ElMessage.success("删除任务已创建");
  } catch {
    ElMessage.error(admin.actionError || "无法删除知识源");
  }
}

async function retry(job: SyncJob) {
  try {
    await admin.retryJob(job);
    ElMessage.success("任务已重新排队");
  } catch {
    ElMessage.error(admin.actionError || "无法重试任务");
  }
}

async function showTurn(turn: QualityTurn) {
  detailOpen.value = true;
  await quality.loadTurnDetail(turn.id);
}

async function deleteTurn(turn: QualityTurn) {
  try {
    await ElMessageBox.confirm("删除后问答、反馈和关联评测用例将一起移除。", "删除问答记录", {
      type: "warning",
      confirmButtonText: "删除",
      cancelButtonText: "取消",
    });
    await quality.deleteTurn(turn.id);
    ElMessage.success("问答记录已删除");
  } catch {
    // Cancel leaves the record unchanged.
  }
}

function openPromotion(turn: QualityTurn) {
  promoteTarget.value = turn;
  evalForm.name = turn.question.slice(0, 80);
  evalForm.requiredTools = "";
  evalForm.citationTypes = "";
  evalForm.requiredFacts = "";
  evalForm.forbiddenFacts = "";
  evalForm.tags = "";
  evalDialogOpen.value = true;
}

function lines(value: string): string[] {
  return value.split(/[\n,，]/).map((item) => item.trim()).filter(Boolean);
}

async function createEvalCase() {
  if (!promoteTarget.value || !evalForm.name.trim()) return;
  try {
    await quality.promoteTurn(promoteTarget.value.id, {
      name: evalForm.name.trim(),
      required_tools: lines(evalForm.requiredTools),
      required_citation_types: lines(evalForm.citationTypes),
      required_facts: lines(evalForm.requiredFacts),
      forbidden_facts: lines(evalForm.forbiddenFacts),
      tags: lines(evalForm.tags),
      enabled: true,
      task_type: "unknown",
      suite: "real-business",
      priority: "normal",
      approval_state: "approved",
    });
    evalDialogOpen.value = false;
    ElMessage.success("已加入评测集");
  } catch {
    ElMessage.error(quality.error || "无法创建评测用例");
  }
}

async function runCases(caseIds: string[]) {
  try {
    const run = await quality.runEvaluation(caseIds);
    await quality.loadEvalRunDetail(run.id);
    runDrawerOpen.value = true;
    pollRun(run.id);
  } catch {
    ElMessage.error(quality.error || "评测运行失败");
  }
}

async function deleteCase(item: EvalCase) {
  try {
    await ElMessageBox.confirm(`确认删除评测用例“${item.name}”？`, "删除评测用例", { type: "warning" });
    await quality.deleteEvalCase(item.id);
  } catch {
    // Cancel leaves the case unchanged.
  }
}

async function selectRun(run: EvalRun) {
  await quality.loadEvalRunDetail(run.id);
  runDrawerOpen.value = true;
  if (["queued", "running"].includes(run.status)) pollRun(run.id);
}

async function approveCase(item: EvalCase, state: "approved" | "rejected") {
  try {
    await quality.setEvalApproval(item, state);
    ElMessage.success(state === "approved" ? "用例已批准" : "用例已驳回");
  } catch {
    ElMessage.error(quality.error || "用例审核失败");
  }
}
</script>

<template>
  <div class="admin-page">
    <AdminHeader />
    <main class="admin-content">
      <el-tabs v-model="activeTab" class="admin-tabs">
        <el-tab-pane label="知识源" name="sources" />
        <el-tab-pane label="问答质量" name="quality" />
        <el-tab-pane label="回归评测" name="evals" />
        <el-tab-pane label="长期记忆" name="memory" />
      </el-tabs>
      <template v-if="activeTab === 'sources'">
      <section class="admin-section" aria-labelledby="sources-heading">
        <div class="section-heading">
          <div>
            <h2 id="sources-heading">知识源</h2>
            <p>管理 Git、文档和 Swagger 数据入口</p>
          </div>
          <div class="section-actions">
            <el-tooltip content="刷新列表" placement="bottom">
              <el-button :icon="Refresh" circle aria-label="刷新列表" :loading="admin.sourcesLoading" @click="admin.loadSources" />
            </el-tooltip>
            <el-button type="primary" :icon="Plus" @click="addDialogOpen = true">添加知识源</el-button>
          </div>
        </div>
        <SourceTable
          :sources="admin.sources"
          :domains="admin.domains"
          :loading="admin.sourcesLoading"
          :error="admin.sourcesError"
          :action-loading="admin.actionLoading"
          @sync="sync"
          @delete="remove"
        />
      </section>

      <section class="admin-section" aria-labelledby="jobs-heading">
        <div class="section-heading">
          <div>
            <h2 id="jobs-heading">同步任务</h2>
            <p>任务状态每 3 秒自动刷新</p>
          </div>
        </div>
        <JobsTable
          :jobs="admin.jobs"
          :sources="admin.sources"
          :loading="admin.jobsLoading"
          :error="admin.jobsError"
          :action-loading="admin.actionLoading"
          @retry="retry"
        />
      </section>
      </template>

      <section v-else-if="activeTab === 'quality'" class="admin-section" aria-labelledby="quality-heading">
        <div class="section-heading">
          <div><h2 id="quality-heading">问答质量</h2><p>查看真实问题、回答、工具路线和用户反馈</p></div>
          <a :href="exportUrl" download><el-button :icon="Download">导出 JSONL</el-button></a>
        </div>
        <div v-if="quality.analytics" class="quality-metrics">
          <div><span>问答数</span><strong>{{ quality.analytics.total_turns }}</strong></div>
          <div><span>引用覆盖率</span><strong>{{ (quality.analytics.citation_coverage * 100).toFixed(1) }}%</strong></div>
          <div><span>P50</span><strong>{{ quality.analytics.p50_duration_ms ? (quality.analytics.p50_duration_ms / 1000).toFixed(1) + 's' : '-' }}</strong></div>
          <div><span>P90</span><strong>{{ quality.analytics.p90_duration_ms ? (quality.analytics.p90_duration_ms / 1000).toFixed(1) + 's' : '-' }}</strong></div>
          <div><span>平均工具数</span><strong>{{ quality.analytics.average_tool_calls.toFixed(1) }}</strong></div>
          <div><span>反馈率</span><strong>{{ (quality.analytics.feedback_rate * 100).toFixed(1) }}%</strong></div>
        </div>
        <div class="quality-filters">
          <el-input v-model="quality.filters.query" clearable placeholder="搜索问题或回答" :prefix-icon="Search" @keyup.enter="quality.loadTurns(1)" />
          <el-select v-model="quality.filters.channel" clearable placeholder="渠道"><el-option label="网页" value="web" /><el-option label="飞书" value="feishu" /><el-option label="Codex" value="codex" /><el-option label="API" value="api" /></el-select>
          <el-select v-model="quality.filters.status" clearable placeholder="状态"><el-option label="完成" value="completed" /><el-option label="失败" value="error" /><el-option label="超时" value="timeout" /><el-option label="待补充" value="clarification_required" /><el-option label="中断" value="interrupted" /></el-select>
          <el-select v-model="quality.filters.rating" clearable placeholder="反馈"><el-option label="赞同" value="positive" /><el-option label="负面" value="negative" /></el-select>
          <el-select v-model="quality.filters.annotationCode" clearable placeholder="问题标签"><el-option v-for="(_, code) in quality.analytics?.issue_counts || {}" :key="code" :label="code" :value="code" /></el-select>
          <el-button type="primary" :icon="Search" @click="refreshQuality">查询</el-button>
        </div>
        <el-alert v-if="quality.error" :title="quality.error" type="error" :closable="false" show-icon />
        <QualityTable :turns="quality.turns" :loading="quality.loading" @select="showTurn" @delete="deleteTurn" @promote="openPromotion" />
        <el-pagination v-if="quality.total > quality.pageSize" v-model:current-page="quality.page" :page-size="quality.pageSize" :total="quality.total" layout="prev, pager, next, total" @current-change="quality.loadTurns" />
        <div v-if="quality.annotations.length" class="annotation-list">
          <h3>待复核问题标签</h3>
          <div v-for="item in quality.annotations.filter((value) => value.review_status === 'pending').slice(0, 20)" :key="item.id" class="annotation-row">
            <span><el-tag size="small" :type="item.severity === 'error' ? 'danger' : 'warning'" effect="plain">{{ item.code }}</el-tag> {{ Math.round(item.confidence * 100) }}%</span>
            <span><el-button link type="success" @click="quality.reviewAnnotation(item.id, 'confirmed')">确认</el-button><el-button link @click="quality.reviewAnnotation(item.id, 'dismissed')">驳回</el-button></span>
          </div>
        </div>
      </section>

      <section v-else-if="activeTab === 'evals'" class="admin-section" aria-labelledby="eval-heading">
        <div class="section-heading"><div><h2 id="eval-heading">回归评测</h2><p>使用最新知识库和工具重跑已筛选的真实问题</p></div></div>
        <EvalCasesTable :cases="quality.evalCases" :runs="quality.evalRuns" :loading="quality.actionLoading" @run="runCases" @delete="deleteCase" @select-run="selectRun" @approve="approveCase" />
      </section>

      <section v-else class="admin-section">
        <MemoryPanel />
      </section>
    </main>
    <AddSourceDialog v-model="addDialogOpen" :domains="admin.domains" @created="admin.loadSources" />
    <QualityDetailDrawer v-model="detailOpen" :turn="quality.selectedTurn" :loading="quality.detailLoading" />
    <el-dialog v-model="evalDialogOpen" title="加入回归评测集" width="min(620px, 92%)">
      <el-form label-position="top">
        <el-form-item label="用例名称" required><el-input v-model="evalForm.name" maxlength="300" /></el-form-item>
        <el-form-item label="必须调用的工具"><el-input v-model="evalForm.requiredTools" placeholder="每行一个，例如 bug_diagnosis_expert" type="textarea" :rows="2" /></el-form-item>
        <el-form-item label="必须包含的引用类型"><el-input v-model="evalForm.citationTypes" placeholder="例如 log_trace, code" /></el-form-item>
        <el-form-item label="必须覆盖的事实"><el-input v-model="evalForm.requiredFacts" placeholder="每行一个关键事实" type="textarea" :rows="3" /></el-form-item>
        <el-form-item label="禁止出现的内容"><el-input v-model="evalForm.forbiddenFacts" type="textarea" :rows="2" /></el-form-item>
        <el-form-item label="标签"><el-input v-model="evalForm.tags" placeholder="例如 bug, 审批流" /></el-form-item>
      </el-form>
      <template #footer><el-button @click="evalDialogOpen = false">取消</el-button><el-button type="primary" :loading="quality.actionLoading" @click="createEvalCase">创建用例</el-button></template>
    </el-dialog>
    <el-drawer v-model="runDrawerOpen" title="评测结果" size="min(760px, 94%)">
      <div v-if="quality.selectedRun" class="run-summary"><div><strong>{{ quality.selectedRun.status }}</strong><span>{{ quality.selectedRun.current_case }}/{{ quality.selectedRun.total_cases }} · {{ quality.selectedRun.passed_cases }} 通过</span></div><div><el-button v-if="['queued','running'].includes(quality.selectedRun.status)" size="small" @click="quality.cancelEvaluation(quality.selectedRun.id)">取消</el-button><el-button v-if="quality.selectedRun.failed_cases" size="small" @click="quality.retryFailedEvaluation(quality.selectedRun.id)">重试失败项</el-button></div></div>
      <article v-for="result in quality.evalResults" :key="result.id" class="result-row"><div><strong :class="result.passed ? 'passed' : 'failed'">{{ result.passed ? '通过' : '未通过' }}</strong><span>{{ quality.evalCases.find((item) => item.id === result.case_id)?.name || result.case_id }}</span><el-tag v-if="result.judge_score !== null" size="small" effect="plain">语义 {{ result.judge_score }}</el-tag><el-tag v-if="result.review_state === 'review_required'" size="small" type="warning">待复核</el-tag></div><p v-if="result.answer">{{ result.answer }}</p><p v-else>{{ result.error_type || '未生成回答' }}</p><div class="result-checks"><el-tag v-for="(passed, name) in result.checks" :key="name" :type="passed ? 'success' : 'danger'" effect="plain">{{ name }}</el-tag></div></article>
    </el-drawer>
  </div>
</template>

<style scoped>
.admin-page {
  min-height: 100vh;
  background: var(--el-fill-color-lighter);
}

.admin-content {
  width: min(1180px, calc(100% - 32px));
  margin: 0 auto;
  padding: 28px 0 48px;
}

.admin-tabs { margin-bottom: 18px; }

.admin-section {
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  background: var(--el-bg-color);
  padding: 20px;
}

.admin-section + .admin-section {
  margin-top: 20px;
}

.section-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
  margin-bottom: 18px;
}

.section-heading h2,
.section-heading p {
  margin: 0;
}

.section-heading h2 {
  color: var(--el-text-color-primary);
  font-size: 18px;
}

.section-heading p {
  margin-top: 4px;
  color: var(--el-text-color-secondary);
  font-size: 13px;
}

.section-actions {
  display: flex;
  align-items: center;
  gap: 10px;
}

.quality-metrics { display: grid; grid-template-columns: repeat(6, minmax(100px, 1fr)); gap: 0; margin: 4px 0 18px; border-block: 1px solid var(--el-border-color-lighter); }
.quality-metrics > div { min-width: 0; padding: 12px; border-right: 1px solid var(--el-border-color-lighter); }
.quality-metrics > div:last-child { border-right: 0; }
.quality-metrics span, .quality-metrics strong { display: block; }
.quality-metrics span { color: var(--el-text-color-secondary); font-size: 11px; }
.quality-metrics strong { margin-top: 5px; font-size: 16px; }
.quality-filters { display: grid; grid-template-columns: minmax(220px, 1fr) repeat(4, 125px) auto; gap: 10px; margin-bottom: 18px; }
.admin-section :deep(.el-pagination) { justify-content: flex-end; margin-top: 18px; }
.run-summary { display: flex; justify-content: space-between; gap: 16px; margin-bottom: 20px; padding-bottom: 14px; border-bottom: 1px solid var(--el-border-color-lighter); }
.result-row { padding: 15px 0; border-bottom: 1px solid var(--el-border-color-lighter); }
.result-row > div:first-child { display: flex; gap: 10px; }
.result-row p { margin: 8px 0; color: var(--el-text-color-regular); font-size: 13px; line-height: 1.6; }
.passed { color: var(--el-color-success); }
.failed { color: var(--el-color-danger); }
.result-checks .el-tag { margin: 0 5px 5px 0; }
.annotation-list { margin-top: 24px; padding-top: 16px; border-top: 1px solid var(--el-border-color-lighter); }
.annotation-list h3 { margin: 0 0 8px; font-size: 14px; }
.annotation-row { display: flex; justify-content: space-between; align-items: center; min-height: 38px; border-bottom: 1px solid var(--el-border-color-extra-light); font-size: 12px; }

@media (max-width: 640px) {
  .admin-content {
    width: calc(100% - 20px);
    padding-top: 16px;
  }

  .admin-section {
    padding: 14px;
  }

  .section-heading {
    align-items: flex-start;
    flex-direction: column;
  }

  .section-actions {
    width: 100%;
    justify-content: flex-end;
  }
  .quality-filters { grid-template-columns: 1fr; }
  .quality-metrics { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .quality-metrics > div:nth-child(2n) { border-right: 0; }
}
</style>
