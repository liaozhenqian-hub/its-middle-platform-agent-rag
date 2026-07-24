<script setup lang="ts">
import { computed, reactive, ref, watch } from "vue";
import { Delete, Plus, UploadFilled } from "@element-plus/icons-vue";
import { ElMessage, ElMessageBox, type UploadFile } from "element-plus";

import { useAdminStore } from "@/stores/admin";
import type { GitLabProject, KnowledgeDomain } from "@/types/api";
import {
  GIT_DOMAIN_IDS,
  createGitPayload,
  gitRulePreset,
  secretFieldsAfterSubmit,
  type GitDomainId,
} from "@/utils/sources";

const props = defineProps<{ modelValue: boolean; domains: KnowledgeDomain[] }>();
const emit = defineEmits<{ "update:modelValue": [value: boolean]; created: [] }>();
const admin = useAdminStore();

const open = computed({
  get: () => props.modelValue,
  set: (value) => emit("update:modelValue", value),
});
const sourceType = ref<"git" | "document" | "swagger">("git");
const documentUpload = ref<File | null>(null);

const git = reactive({
  projectId: "",
  name: "",
  branch: "",
  patterns: gitRulePreset(""),
});
const document = reactive({ name: "", domain_id: "", version: "v1" });
const swagger = reactive({
  name: "",
  domain_id: "",
  url: "",
  auth_type: "none" as "none" | "basic" | "bearer",
  username: "",
  password: "",
  bearer_token: "",
  timeout_seconds: 15,
});

const selectedProject = computed(() =>
  admin.projects.find((project) => String(project.id) === git.projectId),
);

watch(
  () => swagger.auth_type,
  (authType) => {
    if (authType !== "basic") {
      swagger.username = "";
      swagger.password = "";
    }
    if (authType !== "bearer") swagger.bearer_token = "";
  },
);

function domainName(id: string): string {
  return props.domains.find((domain) => domain.id === id)?.name ?? id;
}

async function selectProject(projectId: string) {
  const project = admin.projects.find((item) => String(item.id) === projectId);
  if (!project) return;
  git.name = project.name;
  git.branch = project.default_branch || "";
  git.patterns = gitRulePreset(project.path_with_namespace);
  await admin.loadGitBranches(project.id);
}

function addPattern(domainId: GitDomainId) {
  git.patterns[domainId].push("");
}

function removePattern(domainId: GitDomainId, index: number) {
  git.patterns[domainId].splice(index, 1);
}

function cloneUrl(project: GitLabProject): string {
  const webUrl = project.web_url.replace(/\/$/, "");
  return webUrl.endsWith(".git") ? webUrl : `${webUrl}.git`;
}

async function submitGit() {
  const project = selectedProject.value;
  const allDomainsHaveRules = GIT_DOMAIN_IDS.every((domainId) =>
    git.patterns[domainId].some((pattern) => pattern.trim()),
  );
  if (!project || !git.name.trim() || !git.branch || !allDomainsHaveRules) {
    ElMessage.warning("请完整填写项目、分支和三个领域规则");
    return;
  }
  try {
    const result = await admin.createGitSource(
      createGitPayload(
        {
          name: git.name.trim(),
          project_id: String(project.id),
          project_path: project.path_with_namespace,
          project_url: cloneUrl(project),
          project_web_url: project.web_url,
          branch: git.branch,
        },
        git.patterns,
      ),
    );
    emit("created");
    open.value = false;
    await ElMessageBox.alert(result.webhook_secret, "Webhook 密钥（仅显示一次）", {
      confirmButtonText: "已保存",
    });
  } catch {
    // The store exposes the server error in the dialog.
  }
}

function selectDocument(file: UploadFile) {
  const raw = file.raw;
  if (!raw || !raw.name.toLowerCase().endsWith(".zip")) {
    documentUpload.value = null;
    ElMessage.warning("请选择 ZIP 文件");
    return;
  }
  documentUpload.value = raw;
}

async function submitDocument() {
  if (!document.name.trim() || !document.domain_id || !/^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$/.test(document.version) || !documentUpload.value) {
    ElMessage.warning("请完整填写文档信息并选择有效的 ZIP 文件");
    return;
  }
  try {
    await admin.createDocumentSource({ ...document, name: document.name.trim(), upload: documentUpload.value });
    emit("created");
    open.value = false;
  } catch {
    // The store exposes the server error in the dialog.
  }
}

async function submitSwagger() {
  const credentialsMissing =
    (swagger.auth_type === "basic" && (!swagger.username || !swagger.password)) ||
    (swagger.auth_type === "bearer" && !swagger.bearer_token);
  if (!swagger.name.trim() || !swagger.domain_id || !swagger.url.trim() || credentialsMissing) {
    ElMessage.warning("请完整填写 Swagger 信息和认证凭据");
    return;
  }
  try {
    await admin.createSwaggerSource({ ...swagger, name: swagger.name.trim(), url: swagger.url.trim() });
    emit("created");
    open.value = false;
  } catch {
    // The store exposes the server error in the dialog.
  } finally {
    Object.assign(swagger, secretFieldsAfterSubmit());
  }
}
</script>

<template>
  <el-dialog v-model="open" title="添加知识源" width="680px" destroy-on-close>
    <el-tabs v-model="sourceType" stretch>
      <el-tab-pane label="Git" name="git">
        <el-form label-position="top" @submit.prevent="submitGit">
          <el-form-item label="GitLab 项目" required>
            <el-select
              v-model="git.projectId"
              filterable
              remote
              reserve-keyword
              placeholder="输入项目名称搜索"
              :remote-method="admin.searchGitProjects"
              :loading="admin.projectsLoading"
              data-testid="git-project"
              @change="selectProject"
            >
              <el-option
                v-for="project in admin.projects"
                :key="project.id"
                :label="project.path_with_namespace"
                :value="String(project.id)"
              />
            </el-select>
          </el-form-item>
          <div class="form-grid">
            <el-form-item label="知识源名称" required><el-input v-model="git.name" /></el-form-item>
            <el-form-item label="分支" required>
              <el-select v-model="git.branch" filterable :loading="admin.branchesLoading">
                <el-option v-for="branch in admin.branches" :key="branch.name" :label="branch.name" :value="branch.name" />
              </el-select>
            </el-form-item>
          </div>
          <p class="form-section-title">领域路径规则</p>
          <div class="domain-rules">
            <el-form-item v-for="domainId in GIT_DOMAIN_IDS" :key="domainId" :label="domainName(domainId)" required>
              <div class="rule-list">
                <div v-for="(_pattern, index) in git.patterns[domainId]" :key="`${domainId}-${index}`" class="rule-row">
                  <el-input
                    v-model="git.patterns[domainId][index]"
                    class="rule-input"
                    :data-testid="`git-rule-${domainId}-${index}`"
                  />
                  <el-tooltip content="删除规则" placement="top">
                    <el-button
                      :icon="Delete"
                      circle
                      aria-label="删除规则"
                      @click="removePattern(domainId, index)"
                    />
                  </el-tooltip>
                </div>
                <el-button :icon="Plus" class="add-rule" @click="addPattern(domainId)">添加规则</el-button>
              </div>
            </el-form-item>
          </div>
        </el-form>
      </el-tab-pane>
      <el-tab-pane label="文档 ZIP" name="document">
        <el-form label-position="top" @submit.prevent="submitDocument">
          <div class="form-grid">
            <el-form-item label="知识源名称" required><el-input v-model="document.name" /></el-form-item>
            <el-form-item label="领域" required>
              <el-select v-model="document.domain_id">
                <el-option v-for="domain in domains" :key="domain.id" :label="domain.name" :value="domain.id" />
              </el-select>
            </el-form-item>
          </div>
          <el-form-item label="版本" required><el-input v-model="document.version" placeholder="例如 v1.0" /></el-form-item>
          <el-upload drag accept=".zip,application/zip" :auto-upload="false" :limit="1" :on-change="selectDocument" :on-remove="() => (documentUpload = null)">
            <el-icon class="upload-icon"><UploadFilled /></el-icon>
            <div>拖放 ZIP 文件，或点击选择</div>
          </el-upload>
        </el-form>
      </el-tab-pane>
      <el-tab-pane label="Swagger" name="swagger">
        <el-form label-position="top" @submit.prevent="submitSwagger">
          <div class="form-grid">
            <el-form-item label="知识源名称" required><el-input v-model="swagger.name" /></el-form-item>
            <el-form-item label="领域" required>
              <el-select v-model="swagger.domain_id">
                <el-option v-for="domain in domains" :key="domain.id" :label="domain.name" :value="domain.id" />
              </el-select>
            </el-form-item>
          </div>
          <el-form-item label="OpenAPI 地址" required><el-input v-model="swagger.url" /></el-form-item>
          <el-form-item label="认证方式">
            <el-segmented v-model="swagger.auth_type" :options="[{ label: '无认证', value: 'none' }, { label: 'Basic', value: 'basic' }, { label: 'Bearer', value: 'bearer' }]" />
          </el-form-item>
          <div v-if="swagger.auth_type === 'basic'" class="form-grid">
            <el-form-item label="用户名" required><el-input v-model="swagger.username" autocomplete="off" /></el-form-item>
            <el-form-item label="密码" required><el-input v-model="swagger.password" type="password" show-password autocomplete="new-password" /></el-form-item>
          </div>
          <el-form-item v-if="swagger.auth_type === 'bearer'" label="Bearer Token" required>
            <el-input v-model="swagger.bearer_token" type="password" show-password autocomplete="new-password" />
          </el-form-item>
          <el-form-item label="超时（秒）"><el-input-number v-model="swagger.timeout_seconds" :min="1" :max="120" /></el-form-item>
        </el-form>
      </el-tab-pane>
    </el-tabs>

    <el-alert v-if="admin.discoveryError || admin.actionError" :title="admin.discoveryError || admin.actionError" type="error" show-icon :closable="false" />
    <template #footer>
      <el-button @click="open = false">取消</el-button>
      <el-button v-if="sourceType === 'git'" type="primary" :loading="admin.actionLoading" @click="submitGit">添加 Git 源</el-button>
      <el-button v-else-if="sourceType === 'document'" type="primary" :loading="admin.actionLoading" @click="submitDocument">上传文档</el-button>
      <el-button v-else type="primary" :loading="admin.actionLoading" @click="submitSwagger">添加 Swagger</el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.form-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 16px;
}

.form-section-title {
  margin: 6px 0 14px;
  color: var(--el-text-color-primary);
  font-size: 14px;
  font-weight: 600;
}

.domain-rules {
  max-height: 46vh;
  overflow-y: auto;
  padding-right: 4px;
}

.rule-list {
  display: grid;
  width: 100%;
  gap: 8px;
}

.rule-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 32px;
  align-items: center;
  gap: 8px;
}

.rule-row :deep(.el-button) {
  width: 32px;
  height: 32px;
}

.add-rule {
  justify-self: start;
}

.upload-icon {
  margin-bottom: 8px;
  font-size: 28px;
}

:deep(.el-select),
:deep(.el-upload),
:deep(.el-upload-dragger) {
  width: 100%;
}

@media (max-width: 640px) {
  .form-grid {
    grid-template-columns: 1fr;
    gap: 0;
  }

  :global(.el-dialog) {
    width: calc(100vw - 24px) !important;
  }
}
</style>
