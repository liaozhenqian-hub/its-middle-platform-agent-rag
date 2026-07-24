# Git Source Environment Label Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在管理端知识源列表明确区分 master 线上代码与 develop 开发/测试代码。

**Architecture:** 保留后端数据模型，通过 Vue 展示函数读取 `KnowledgeSource.config.branch` 和 `project_path`，生成分支标签与环境备注。该变更只影响展示，不改变同步身份或数据。

**Tech Stack:** Vue 3、TypeScript、Element Plus、Vitest

---

### Task 1: Environment Presentation

**Files:**
- Modify: `web/src/components/admin/SourceTable.vue`
- Create: `web/src/components/admin/SourceTable.test.ts`

- [ ] **Step 1: Write the failing component test**

挂载三个 Git 来源，分别使用 backend master、frontend master 和 backend develop，断言页面出现“线上后端”“线上前端”“开发 / 测试后端”及对应分支。

- [ ] **Step 2: Run the focused test and confirm RED**

Run: `npm test -- --run src/components/admin/SourceTable.test.ts`

- [ ] **Step 3: Implement display helpers and markup**

从 `source.config.branch` 和 `source.config.project_path` 安全读取字符串；Git 来源使用名称、分支 tag 和环境备注组合展示，非 Git 来源不变。

- [ ] **Step 4: Run focused and full frontend verification**

Run: `npm test -- --run src/components/admin/SourceTable.test.ts`, `npm test`, `npm run build`。

- [ ] **Step 5: Restart FastAPI static frontend and verify readiness**

保持 `--workers 1`，确认 `/health/ready` 为 ready。

## Repository Note

目标目录不是 Git 仓库，不执行 commit、branch 或 PR。
