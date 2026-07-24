from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from typing import Protocol

from pydantic import ValidationError

from knowledge.bug_graph.models import BugIntake, BugIntakeCandidate, Environment


_TRACE_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{6,200}$")
_TRACE_IN_MESSAGE_PATTERN = re.compile(
    r"(?i)(?:trace\s*id|traceid|trace_id|raceid|raceld|traceld|链路\s*id)\s*"
    r"(?:是|为|[:：=])?\s*([A-Za-z0-9._:-]{6,200})"
)
_STANDALONE_UUID_PATTERN = re.compile(
    r"^[A-Fa-f0-9]{8}-[A-Fa-f0-9]{4}-[A-Fa-f0-9]{4}-"
    r"[A-Fa-f0-9]{4}-[A-Fa-f0-9]{12}$"
)
_REQUEST_TIME_PATTERN = re.compile(
    r"(?i)(?:请求时间|报错时间|发生时间|异常时间|时间)\s*"
    r"(?:是|为|[:：=])?\s*"
    r"(\d{4}[-/]\d{1,2}[-/]\d{1,2}[ T]\d{1,2}:\d{2}"
    r"(?::\d{2})?(?:Z|[+-]\d{2}:?\d{2})?)"
)
_CHINA_TIMEZONE = timezone(timedelta(hours=8))
_ENVIRONMENT_ALIASES: dict[Environment, tuple[str, ...]] = {
    "develop": ("开发环境", "开发", "develop", "dev"),
    "test": ("测试环境", "测试", "test"),
    "prod": ("线上环境", "线上", "生产环境", "生产", "production", "prod"),
}


class IntakeNormalizer(Protocol):
    async def normalize(
        self,
        message: str,
        validation_feedback: str | None = None,
    ) -> str: ...


class BugIntakeParser:
    def __init__(self, normalizer: IntakeNormalizer | None = None):
        self.normalizer = normalizer

    async def parse(
        self,
        message: str,
        *,
        normalize: bool = True,
        allow_standalone_trace: bool = False,
    ) -> BugIntake:
        normalized_message = message.strip()
        candidate = (
            await self._model_candidate(normalized_message)
            if normalize
            else BugIntakeCandidate(normalized_problem=normalized_message)
        )
        environment, environment_evidence = self._detect_environment(
            normalized_message
        )
        trace_id = self._verified_trace(normalized_message, candidate.trace_id)
        if trace_id is None:
            trace_match = _TRACE_IN_MESSAGE_PATTERN.search(normalized_message)
            trace_id = trace_match.group(1) if trace_match else None
        if trace_id is None and allow_standalone_trace:
            standalone = normalized_message.strip(" \t\r\n,，。")
            if _STANDALONE_UUID_PATTERN.fullmatch(standalone):
                trace_id = standalone
        request_time, request_time_evidence = self._detect_request_time(
            normalized_message
        )

        missing_fields = []
        if environment is None:
            missing_fields.append("environment")
        if trace_id is None:
            missing_fields.append("trace_id")
        question = self._clarification_question(missing_fields)
        return BugIntake(
            **candidate.model_dump(
                exclude={
                    "normalized_problem",
                    "environment",
                    "environment_evidence",
                    "trace_id",
                    "request_time",
                    "request_time_evidence",
                }
            ),
            original_message=normalized_message,
            environment=environment,
            environment_evidence=environment_evidence,
            trace_id=trace_id,
            request_time=request_time,
            request_time_evidence=request_time_evidence,
            missing_fields=missing_fields,
            clarification_question=question,
            normalized_problem=candidate.normalized_problem or normalized_message,
        )

    async def _model_candidate(self, message: str) -> BugIntakeCandidate:
        if self.normalizer is None:
            return BugIntakeCandidate(normalized_problem=message)
        feedback = None
        for _ in range(2):
            raw = await self.normalizer.normalize(message, feedback)
            try:
                return BugIntakeCandidate.model_validate(self._json_object(raw))
            except (ValueError, ValidationError, json.JSONDecodeError) as exc:
                feedback = f"Return one valid JSON object. Validation error: {exc}"
        return BugIntakeCandidate(normalized_problem=message)

    @staticmethod
    def _json_object(raw: str) -> dict:
        value = raw.strip()
        if value.startswith("```"):
            value = re.sub(r"^```(?:json)?\s*", "", value, flags=re.IGNORECASE)
            value = re.sub(r"\s*```$", "", value)
        start = value.find("{")
        end = value.rfind("}")
        if start < 0 or end < start:
            raise ValueError("JSON object not found")
        parsed = json.loads(value[start : end + 1])
        if not isinstance(parsed, dict):
            raise ValueError("JSON object is required")
        return parsed

    @staticmethod
    def _detect_environment(message: str) -> tuple[Environment | None, str]:
        lowered = message.lower()
        matches: dict[Environment, str] = {}
        for environment, aliases in _ENVIRONMENT_ALIASES.items():
            for alias in aliases:
                if re.search(rf"(?<![a-z]){re.escape(alias.lower())}(?![a-z])", lowered):
                    matches[environment] = alias
                    break
        if len(matches) != 1:
            return None, ""
        environment = next(iter(matches))
        return environment, matches[environment]

    @staticmethod
    def _verified_trace(message: str, candidate: str | None) -> str | None:
        if not candidate:
            return None
        normalized = candidate.strip()
        if normalized not in message or not _TRACE_PATTERN.fullmatch(normalized):
            return None
        return normalized

    @staticmethod
    def _detect_request_time(message: str) -> tuple[str | None, str]:
        match = _REQUEST_TIME_PATTERN.search(message)
        if match is None:
            return None, ""
        evidence = match.group(1)
        value = evidence.replace("/", "-")
        if value.endswith("Z"):
            value = f"{value[:-1]}+00:00"
        if re.search(r"[+-]\d{4}$", value):
            value = f"{value[:-5]}{value[-5:-2]}:{value[-2:]}"
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None, ""
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=_CHINA_TIMEZONE)
        return parsed.isoformat(timespec="seconds"), evidence

    @staticmethod
    def _clarification_question(missing_fields: list[str]) -> str:
        if missing_fields == ["environment", "trace_id"]:
            return "请补充问题环境（开发、测试或生产）和 trace ID。"
        if missing_fields == ["environment"]:
            return "请确认问题发生在开发、测试还是生产环境。"
        if missing_fields == ["trace_id"]:
            return "请补充该请求的 trace ID。"
        return ""
