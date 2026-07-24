from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class GuardDecision:
    behavior: str
    answer: str


class RequestGuard:
    """Handle deterministic no-tool requests before invoking an LLM."""

    _SENSITIVE = (
        "银行卡密码",
        "同事密码",
        "他人密码",
        "账号口令",
        "个人密码",
    )
    _WRITE_ACTIONS = ("删除", "修改", "新增", "创建", "写入", "下线")
    _WRITE_TARGETS = ("指标", "数据库", "审批", "工作流", "接口")
    _EXPLANATORY = ("怎么", "如何", "能不能", "能否", "是否", "规则", "流程")
    _EXECUTION = ("直接", "立即", "马上", "调用", "替我", "帮我", "无需", "不需要", "执行")

    def evaluate(self, message: str) -> GuardDecision | None:
        normalized = "".join(message.casefold().split())
        if any(marker in normalized for marker in self._SENSITIVE):
            return GuardDecision(
                behavior="refuse",
                answer=(
                    "不能查询、猜测或泄露他人的银行卡密码、账号口令或其他敏感凭证。"
                    "我只能协助审批流、工作流、指标平台对接，需求分析，以及带环境和 trace ID 的故障定位。"
                ),
            )
        if (
            any(action in normalized for action in self._WRITE_ACTIONS)
            and any(target in normalized for target in self._WRITE_TARGETS)
            and any(marker in normalized for marker in self._EXECUTION)
            and not any(marker in normalized for marker in self._EXPLANATORY)
        ):
            return GuardDecision(
                behavior="refuse",
                answer=(
                    "不能执行新增、修改、删除或下线等写操作；当前工具仅允许中台知识检索和只读查询。"
                    "如需评估变更，请描述目标、原因和影响范围，我可以提供实施建议。"
                ),
            )
        return None
