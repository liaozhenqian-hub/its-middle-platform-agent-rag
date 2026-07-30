# SSE 数据库异常容错设计

## 目标

数据库连接在问答预处理阶段中断时，聊天流必须及时向客户端发送可识别的开始和失败事件，前端不得展示浏览器原始的 `network error`，也不得让用户无限等待。

## 后端设计

- `/api/v1/agent/chat/stream` 的事件生成器先发送一次 API 层 `run.started`，再执行质量记录和 Agent 流。
- Agent 流返回的重复 `run.started` 由 API 层过滤，保持公开事件契约只有一次开始事件。
- Agent 流在产生终止事件前抛出异常时，API 层记录脱敏异常类型并发送一次 `run.error`；不把异常正文、数据库地址或凭证返回给用户。
- 质量记录继续采用 best-effort 语义，开始或完成记录失败不得覆盖主链路错误。
- 已由 Agent 正常产生的 `run.error`、`run.completed` 和 `approval.required` 保持现有行为。

## 前端设计

- 将 `network error`、`Failed to fetch`、`NetworkError` 和 `Load failed` 统一转换为中文可操作提示。
- 流异常且没有任何回答正文时，助手消息展示同一条中文提示，不再只显示“本次请求未完成”。
- 已有后端中文 `run.error` 原样展示，不覆盖更准确的服务端提示。

## 验证

- 后端测试模拟 Agent 在首个事件前抛出数据库异常，断言仍收到一次 `run.started` 和一次脱敏 `run.error`。
- 后端现有 SSE 成功用例断言 `run.started` 不重复。
- 前端测试模拟浏览器抛出 `network error`，断言页面状态和助手消息均为中文提示。
- 运行相关 Python、Vitest 回归和前端构建。
