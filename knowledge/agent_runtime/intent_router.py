from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class RoutingDecision:
    domains: tuple[str, ...]
    intent: str
    confidence: float
    needs_clarification: bool = False
    reason_codes: tuple[str, ...] = ()
    task_type: str = "unknown"
    route_source: str = "rules"
    duration_ms: float | None = None


class DomainIntentRouter:
    _APPROVAL = (
        "审批",
        "会签",
        "或签",
        "顺签",
        "审批人",
        "撤回",
        "加签",
        "抄送",
        "转审",
        "流程改版",
        "流程版本",
        "老单子",
        "流程迁移",
    )
    _WORKFLOW = (
        "工作流",
        "连接器",
        "http节点",
        "http任务",
        "上游节点",
        "下游节点",
        "回调节点",
        "for节点",
        "switch",
        "默认分支",
        "节点变量",
        "异常分支",
        "循环节点",
        "上一个节点",
        "下一个节点",
        "http请求",
        "接口返回",
        "接口回",
        "规则节点",
        "请求body",
    )
    _METRIC = (
        "指标",
        "原子指标",
        "派生指标",
        "复合指标",
        "业务指标",
        "指标应用",
        "指标口径",
        "查数",
        "cube",
        "包裹数",
        "mcp",
    )
    _STRONG_BUG = (
        "traceid",
        "trace id",
        "stacktrace",
        "500",
        "502",
        "503",
    )
    _WEAK_BUG = ("报错", "异常", "错误码")
    _DIAGNOSIS = ("定位", "排查", "查日志", "根因", "为什么报错")
    _VAGUE = ("这个", "那个", "不对", "有问题", "怎么弄", "帮我看看")

    def route(self, message: str) -> RoutingDecision:
        normalized = self._normalize(message)
        task_type = self._task_type(normalized)
        matched: list[tuple[str, str]] = []
        if any(marker in normalized for marker in self._APPROVAL):
            matched.append(("approval-flow", "approval_term"))
        if self._is_workflow(normalized):
            matched.append(("workflow", "workflow_term"))
        if any(marker in normalized for marker in self._METRIC):
            matched.append(("metric-platform", "metric_term"))

        strong_bug = any(marker in normalized for marker in self._STRONG_BUG)
        weak_bug = any(marker in normalized for marker in self._WEAK_BUG)
        diagnosis_requested = any(marker in normalized for marker in self._DIAGNOSIS)
        domain_lookup_requested = (
            bool(matched)
            and self._is_domain_lookup(normalized)
            and not diagnosis_requested
        )
        if (strong_bug or (weak_bug and (not matched or diagnosis_requested))) and not domain_lookup_requested:
            return RoutingDecision(
                domains=("bug",),
                intent="bug",
                confidence=1.0,
                reason_codes=("explicit_error_signal",),
                task_type="bug",
            )

        if domain_lookup_requested:
            task_type = "api_contract"

        domains = tuple(item[0] for item in matched)
        reason_codes = tuple(item[1] for item in matched)
        if not domains:
            return RoutingDecision((), "unknown", 0.0, task_type=task_type)
        if len(domains) > 1:
            return RoutingDecision(
                domains=domains,
                intent="cross-domain",
                confidence=1.0,
                reason_codes=reason_codes,
                task_type=task_type,
            )
        vague = (
            False
            if domain_lookup_requested
            else self._is_vague(normalized, domains[0])
        )
        return RoutingDecision(
            domains=domains,
            intent=domains[0],
            confidence=0.99,
            needs_clarification=vague,
            reason_codes=reason_codes,
            task_type=(
                "metric_query"
                if domains == ("metric-platform",)
                and task_type in {"how_to", "unknown"}
                and any(marker in normalized for marker in ("查", "多少", "数据", "sql", "趋势", "每天"))
                else task_type
            ),
        )

    @staticmethod
    def _task_type(normalized: str) -> str:
        if any(marker in normalized for marker in ("traceid", "stacktrace", "报错", "查日志", "根因")):
            return "bug"
        if any(marker in normalized for marker in ("需求", "可行性", "是否合理", "影响哪些", "影响范围")):
            return "requirement_analysis"
        if (
            any(marker in normalized for marker in ("接口", "api", "url", "路径"))
            and any(marker in normalized for marker in ("入参", "出参", "请求", "响应", "字段", "参数"))
        ):
            return "api_contract"
        if any(marker in normalized for marker in ("代码", "controller", "service", "class", "方法在哪", "实现在哪")):
            return "code_lookup"
        if any(marker in normalized for marker in ("怎么", "如何", "对接", "配置", "使用", "步骤")):
            return "how_to"
        if any(marker in normalized for marker in ("指标", "查数", "包裹数", "sql")):
            return "metric_query"
        return "unknown"

    @classmethod
    def _is_workflow(cls, normalized: str) -> bool:
        if any(marker in normalized for marker in cls._WORKFLOW):
            return True
        return (
            "节点" in normalized
            and any(
                marker in normalized
                for marker in ("变量", "分支", "重试", "超时", "顺序", "覆盖", "规则")
            )
        ) or (
            "实例" in normalized
            and any(marker in normalized for marker in ("并发", "乱序", "重复执行"))
        )

    @staticmethod
    def _is_domain_lookup(normalized: str) -> bool:
        interface_signal = any(
            marker in normalized for marker in ("接口", "api", "url", "路径", "sdk")
        )
        existence_signal = any(
            marker in normalized
            for marker in ("是否有", "有没有", "是否存在", "有这个接口", "存在这个接口")
        )
        return interface_signal and existence_signal

    @classmethod
    def _is_vague(cls, normalized: str, domain: str) -> bool:
        if not any(marker in normalized for marker in cls._VAGUE):
            return False
        concrete_markers = {
            "approval-flow": cls._APPROVAL[1:],
            "workflow": cls._WORKFLOW[1:],
            "metric-platform": cls._METRIC[1:],
        }[domain]
        return not any(marker in normalized for marker in concrete_markers)

    @staticmethod
    def _normalize(message: str) -> str:
        return re.sub(r"[\s_\-]+", "", message.strip().casefold())
