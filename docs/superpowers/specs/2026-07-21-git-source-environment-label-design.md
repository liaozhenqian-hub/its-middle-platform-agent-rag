# Git 知识源环境标签设计

## 目标

管理端来源列表必须能区分同一 GitLab 项目的 `master` 与 `develop` 来源，避免管理员把线上代码和开发/测试代码混淆。

## 设计

- 不修改来源名称、source ID、Webhook、目录规则或同步配置。
- Git 来源名称下方显示环境备注，名称右侧显示真实分支标签。
- `master` 显示“线上代码”；`develop` 显示“开发 / 测试代码”；其他分支显示“代码分支”。
- 根据 `project_path` 是否以 `-web` 结尾，在备注中补充“前端”或“后端”。
- 非 Git 来源保持现有名称展示，不显示分支备注。

## 验收

- 后端 master：`线上后端 · master`。
- 前端 master：`线上前端 · master`。
- 后端 develop：`开发 / 测试后端 · develop`。
- 缺少 branch 时不显示误导性环境标签。
- SourceTable 组件测试和生产构建通过。
