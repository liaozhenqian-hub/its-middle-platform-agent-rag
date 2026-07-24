from __future__ import annotations

import re

from knowledge.memory.models import ProceduralSpec, ProceduralStep


class ProceduralMemoryValidator:
    ALLOWED_CAPABILITIES = frozenset({
        "query_trace_logs",
        "extract_log_signals",
        "search_branch_code",
        "inspect_contract_and_docs",
        "validate_fix",
    })
    EVIDENCE_GRADES = frozenset({"correlated", "contract_supported"})
    _UNSAFE = re.compile(
        r"authorization\s*:|bearer\s+|cookie\s*:|password|token\s*[=:]|"
        r"https?://|logql|chain[- ]?of[- ]?thought|思考过程|完整日志|代码正文",
        re.IGNORECASE,
    )

    def validate(self, spec: ProceduralSpec) -> ProceduralSpec:
        text_values = [
            spec.task_type, spec.minimum_evidence_grade,
            *spec.trigger_conditions, *spec.required_inputs,
            *spec.environment_constraints, *spec.branch_constraints,
            *spec.allowed_tools, *spec.stop_conditions, *spec.fallback_actions,
            *spec.expected_output, *spec.validation_steps,
        ]
        for step in spec.steps:
            text_values.extend((
                step.capability, step.purpose, *step.required_inputs,
                *step.produced_signals, step.next_condition or "",
            ))
        if (
            spec.task_type != "bug_diagnosis"
            or spec.procedure_version < 1
            or not spec.steps
            or spec.minimum_evidence_grade not in self.EVIDENCE_GRADES
            or any(item not in self.ALLOWED_CAPABILITIES for item in spec.allowed_tools)
            or any(step.capability not in self.ALLOWED_CAPABILITIES for step in spec.steps)
            or any(self._UNSAFE.search(str(value)) for value in text_values)
        ):
            raise ValueError("unsafe procedural memory")
        return spec


def build_bug_diagnosis_spec(*, environment: str, branch: str) -> ProceduralSpec:
    spec = ProceduralSpec(
        task_type="bug_diagnosis",
        procedure_version=2,
        trigger_conditions=("bug_report", "trace_id_present"),
        required_inputs=("environment", "trace_id"),
        environment_constraints=(environment,),
        branch_constraints=(branch,),
        steps=(
            ProceduralStep("query_trace_logs", "查询最近二十四小时脱敏日志", ("environment", "trace_id"), ("log_signals",), "logs_found"),
            ProceduralStep("extract_log_signals", "提取异常类型、堆栈符号与接口路径", ("log_signals",), ("code_hints",), "signals_found"),
            ProceduralStep("search_branch_code", "在固定分支检索符号和相关代码", ("code_hints", "branch"), ("code_evidence",), "code_evidence_found"),
            ProceduralStep("inspect_contract_and_docs", "按已确认接口补充契约与文档", ("code_evidence",), ("contract_evidence",), "optional"),
            ProceduralStep("validate_fix", "形成修复方案并列出验证步骤", ("code_evidence",), ("diagnosis",), None),
        ),
        allowed_tools=(
            "query_trace_logs", "extract_log_signals", "search_branch_code",
            "inspect_contract_and_docs", "validate_fix",
        ),
        minimum_evidence_grade="correlated",
        stop_conditions=("missing_environment", "missing_trace_id", "no_logs"),
        fallback_actions=("request_missing_field", "report_no_logs"),
        expected_output=(
            "problem_summary", "confirmed_facts", "code_location",
            "possible_cause", "fix", "validation_steps", "missing_information",
        ),
        validation_steps=("requery_current_logs", "verify_current_code", "verify_target_environment"),
    )
    return ProceduralMemoryValidator().validate(spec)
