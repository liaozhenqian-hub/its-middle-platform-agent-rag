from types import SimpleNamespace

import pytest

from knowledge.feishu.messages import (
    adapt_markdown_for_feishu,
    format_agent_reply,
    parse_message_event,
    split_reply,
)


def payload(**overrides):
    value = {
        "event_id": "event-1",
        "sender_type": "user",
        "sender_id": "ou-user-1",
        "sender_name": "张三",
        "message_id": "message-1",
        "chat_id": "chat-1",
        "chat_type": "p2p",
        "message_type": "text",
        "content": '{"text":"怎么对接工作流"}',
        "mentions": [],
    }
    value.update(overrides)
    return value


def test_parse_private_text_message():
    message = parse_message_event(payload(), require_group_mention=True)

    assert message is not None
    assert message.event_id == "event-1"
    assert message.text == "怎么对接工作流"
    assert message.sender_id == "ou-user-1"
    assert message.sender_name == "张三"


def test_parse_group_message_requires_and_removes_bot_mention():
    accepted = parse_message_event(
        payload(
            chat_type="group",
            content='{"text":"@_user_1 帮我分析这个 Bug"}',
            mentions=[{"key": "@_user_1", "open_id": "ou_bot"}],
        ),
        require_group_mention=True,
        bot_open_id="ou_bot",
    )
    rejected = parse_message_event(
        payload(chat_type="group", content='{"text":"普通群消息"}'),
        require_group_mention=True,
        bot_open_id="ou_bot",
    )

    assert accepted is not None
    assert accepted.text == "帮我分析这个 Bug"
    assert rejected is None


def test_parse_group_message_ignores_mentions_of_other_users():
    message = parse_message_event(
        payload(
            chat_type="group",
            content='{"text":"@_user_1 请处理这个问题"}',
            mentions=[{"key": "@_user_1", "open_id": "ou_other_user"}],
        ),
        require_group_mention=True,
        bot_open_id="ou_bot",
    )

    assert message is None


def test_parse_group_message_fails_closed_when_bot_identity_is_unavailable():
    message = parse_message_event(
        payload(
            chat_type="group",
            content='{"text":"@_user_1 请处理这个问题"}',
            mentions=[{"key": "@_user_1", "open_id": "ou_bot"}],
        ),
        require_group_mention=True,
        bot_open_id="",
    )

    assert message is None


@pytest.mark.parametrize(
    "value",
    [
        payload(sender_type="app"),
        payload(message_type="image"),
        payload(content="not-json"),
        payload(content='{"text":"   "}'),
    ],
)
def test_parse_ignores_unsafe_or_unsupported_events(value):
    assert parse_message_event(value, require_group_mention=True) is None


def test_format_agent_reply_exposes_only_public_citation_identity():
    response = SimpleNamespace(
        answer="请按文档完成配置。",
        citations=[
            SimpleNamespace(
                source_type="code",
                source_id="code-1",
                title="WorkflowService.create",
                metadata={
                    "content": "must-not-appear",
                    "token": "secret",
                    "branch": "master",
                    "relative_path": "workflow/WorkflowService.java",
                    "gitlab_url": (
                        "https://gitlab.example/project/-/blob/abc/"
                        "WorkflowService.java#L10"
                    ),
                },
            ),
            SimpleNamespace(
                source_type="log_trace",
                source_id="trace-1",
                title="test trace",
                metadata={"raw_logs": "must-not-appear"},
            ),
        ],
    )

    output = format_agent_reply(response)

    assert output.startswith("请按文档完成配置。")
    assert "**引用依据**" in output
    assert (
        "**代码** · [代码：WorkflowService.java / WorkflowService.create]"
        "(https://gitlab.example/project/-/blob/abc/WorkflowService.java#L10)"
    ) in output
    assert "master · workflow/WorkflowService.java" in output
    assert "**日志 Trace** · 目标环境日志证据" in output
    assert "must-not-appear" not in output
    assert "secret" not in output


def test_format_agent_reply_never_displays_internal_citation_ids():
    response = SimpleNamespace(
        answer="证据来自 code-889c460d7d7d4e46a824。",
        citations=[
            SimpleNamespace(
                source_type="code",
                source_id="code-889c460d7d7d4e46a824",
                title="",
                domain="审批流",
                metadata={
                    "relative_path": "approval/TransferService.java",
                    "symbol_name": "TransferService.run",
                },
            )
        ],
    )

    output = format_agent_reply(response)

    assert "code-889c" not in output
    assert "代码：TransferService.java / TransferService.run" in output


def test_adapt_markdown_for_feishu_converts_tables_to_readable_sections():
    markdown = """三种模式

| 模式 | 表现 | 是否符合预期 |
| --- | --- | --- |
| 会签（mode=1） | 所有人同时审批 | 否 |
| 顺签（mode=3） | 按添加顺序审批 | 是 |

结论不变。"""

    output = adapt_markdown_for_feishu(markdown)

    assert "**会签（mode=1）**" in output
    assert "- **表现**：所有人同时审批" in output
    assert "- **是否符合预期**：是" in output
    assert "| --- |" not in output
    assert output.endswith("结论不变。")


def test_adapt_markdown_for_feishu_formats_api_document_for_cards():
    markdown = """# 审批流管理员转办接口

## 1. 查询管理员可转办节点/人员

### 接口信息

| 项目 | 内容 |
|---|---|
| URL | `/sys/flow/task/getAdminTransferOptions` |
| 请求方式 | `POST` |
| Controller | `ProcessTaskController#getAdminTransferOptions` |

请求体：`AdminTransferOptionsReqVO`

```json
{"processInstanceId": "流程实例ID"}
```"""

    output = adapt_markdown_for_feishu(markdown)

    assert "**审批流管理员转办接口**" in output
    assert "**1. 查询管理员可转办节点/人员**" in output
    assert "**接口信息**" in output
    assert "**URL**：/sys/flow/task/getAdminTransferOptions" in output
    assert "**请求方式**：POST" in output
    assert "**Controller**：ProcessTaskController#getAdminTransferOptions" in output
    assert "请求体：AdminTransferOptionsReqVO" in output
    assert "```json\n{\"processInstanceId\": \"流程实例ID\"}\n```" in output
    assert not any(line.lstrip().startswith("#") for line in output.splitlines())
    assert "- **内容**" not in output


def test_split_reply_prefers_paragraph_boundaries_and_hard_splits_long_blocks():
    text = "第一段" + "a" * 12 + "\n\n第二段\n\n" + "b" * 25

    chunks = split_reply(text, max_chars=20)

    assert "".join(chunks).replace("\n\n", "") == text.replace("\n\n", "")
    assert all(0 < len(chunk) <= 20 for chunk in chunks)
