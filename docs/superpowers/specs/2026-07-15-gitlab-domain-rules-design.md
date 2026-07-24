# GitLab 中台代码分域规则设计

日期：2026-07-15

## 目标

将以下两个 GitLab 项目作为两个独立 Git 知识来源接入同一个“中台”知识空间，并依据真实仓库路径把代码划分为指标平台、审批流、工作流或 `shared`：

- `erp/loctek-middle-platform`，默认分支 `master`
- `erp/loctek-middle-platform-web`，默认分支 `master`

GitLab 访问保持只读。未匹配的登录、权限、基础设施和公共组件代码进入 `shared`，供三个领域专家共同检索。

## 管理端规则模型

管理端不再限制每个领域只能填写一个 glob。每个领域显示可增删的规则列表，提交时每个 glob 转换为一条独立的 `SourceDomainRule`。

选择已知项目后，页面按 `path_with_namespace` 自动填入对应预设；管理员仍可在创建来源前编辑。未知项目继续提供空规则编辑器，不根据项目名称猜测领域。

规则按升序优先级匹配，第一条命中的规则生效。预设规则使用稳定、互不重叠的优先级；没有命中的路径由现有分类器回退到 `shared`。

## 后端仓库预设

项目：`erp/loctek-middle-platform`

### 指标平台

- `docs/datacenter/**`
- `**/datacenter/**`
- `**/metric/**`
- `**/cube/**`
- `skills/mp-backend-datacenter-*/**`

### 审批流

- `docs/flow/**`
- `**/flow/**`
- `**/common-flowable/**`
- `**/larkApprove/**`
- `**/lark/approve/**`
- `skills/mp-backend-approval-flow/**`

### 工作流

- `**/workflow/**`
- `**/common-liteflow/**`
- `skills/mp-backend-workflow/**`

`flow` 按完整路径段匹配审批流，不使用包含关系判断，因此不会把 `workflow` 错归为审批流。

## 前端仓库预设

项目：`erp/loctek-middle-platform-web`

### 指标平台

- `src/views/digitalIntelligenceCenter/indicatorPlatform/**`
- `src/api/digitalIntelligenceCenter/**`
- `.trae/skills/complex-indicator-handoff/**`

### 审批流

- `src/views/approvalCenter/**`
- `src/api/process/**`
- `src/stores/approval.js`
- `src/components/drawer/approverDrawer.vue`

### 工作流

- `src/views/integrationCenter/workflow/**`
- `src/api/integrationCenter/workflow/**`
- `.trae/skills/workflow-branch-chain/**`
- `src/css/workflow.css`
- `src/utils/workflow.js`

## Webhook Secret 存储

Git 来源创建时生成高熵 Webhook Secret，只向管理员显示一次。SQLite 只保存该 Secret 的 SHA-256 哈希，Webhook 校验继续使用恒定时间比较。

哈希不需要可逆解密，因此新增专用的 Webhook Secret 哈希存储，不再通过 `CatalogSecretStore` 保存。Git 来源创建不依赖 `KNOWLEDGE_SECRET_MASTER_KEY`；该主密钥只用于 Swagger Basic/Bearer 等必须在调用时解密的动态凭证。

现有已经通过加密存储保存的 Webhook 哈希在读取时保持兼容，直到来源重新生成或迁移，避免破坏已有来源。

## 数据流

1. 管理员搜索并选择 GitLab 项目。
2. 前端根据项目路径载入后端或前端规则预设。
3. 管理员选择分支并检查或调整多条规则。
4. 前端将规则展开为有序数组提交给现有 Git 来源 API。
5. 后端创建来源、规则和 Webhook Secret 哈希。
6. Worker 同步代码时按规则优先级分类，未命中路径写入 `shared`。

## 错误处理

- 每个领域至少需要一条非空规则；空白规则在前端提交前清理。
- 后端继续校验目标领域 ID，拒绝未知领域。
- 重复规则在前端规范化后去重，避免无意义的优先级冲突。
- 未识别项目不自动套用错误预设，管理员必须明确填写规则。
- GitLab、分支或同步失败继续走现有任务失败与重试流程。

## 验证

- 单元测试覆盖两个项目预设生成的 payload、规则增删和去重。
- 分类器测试覆盖后端与前端各领域的代表路径，并验证 `flow` 不会匹配 `workflow`。
- 测试未匹配公共路径回退到 `shared`。
- API 测试验证未配置主密钥时仍能创建 Git 来源并校验 Webhook。
- 兼容性测试验证 Swagger 凭证仍要求主密钥，且旧式加密 Webhook 哈希仍可读取。
- 运行后端完整 pytest、前端 Vitest 和生产构建。
