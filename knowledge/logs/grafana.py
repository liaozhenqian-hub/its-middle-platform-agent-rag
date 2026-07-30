from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass
from typing import Any

import httpx


_TRACE_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{6,200}$")
_EXCEPTION_PATTERN = re.compile(
    r"\b(?:[A-Za-z_$][\w$]*\.)*([A-Za-z_$][\w$]*(?:Exception|Error))\b"
)
_STACK_FRAME_PATTERN = re.compile(
    r"\bat\s+([\w.$]+)\(([^():]+)(?::(\d+))?\)"
)
_ENDPOINT_PATTERN = re.compile(
    r"\b(?:GET|POST|PUT|PATCH|DELETE|OPTIONS|HEAD)\s+(/[A-Za-z0-9_./{}:?=&%+-]+)",
    re.IGNORECASE,
)
_SECRET_PATTERN = re.compile(
    r"(?i)\b(authorization|cookie|set-cookie|access[_-]?token|refresh[_-]?token|"
    r"api[_-]?key|password|passwd|secret|token)\b(\s*[:=]\s*)([^\s,;]+)"
)
_BEARER_PATTERN = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
_EMAIL_PATTERN = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_PHONE_PATTERN = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
_LABEL_VALUE_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")
_SPRING_LOGGER_PATTERN = re.compile(
    r"\]\s+---\s+\[[^\]]+\]\s+([A-Za-z_$][\w.$]+)\s+:"
)


class GrafanaLogError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        retryable: bool = False,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.status_code = status_code


@dataclass(frozen=True)
class GrafanaTarget:
    datasource_uid: str
    namespace: str
    code_branch: str


@dataclass(frozen=True)
class StackFrame:
    symbol: str
    file: str
    line: int | None = None


@dataclass(frozen=True)
class LogEntry:
    timestamp: str
    level: str
    logger: str
    message: str
    exception_types: tuple[str, ...] = ()
    stack_frames: tuple[StackFrame, ...] = ()


@dataclass(frozen=True)
class TraceLogResult:
    trace_id: str
    environment: str
    code_branch: str
    from_ms: int
    to_ms: int
    entries: tuple[LogEntry, ...]
    exception_types: tuple[str, ...]
    truncated: bool = False
    service_names: tuple[str, ...] = ()
    endpoint_paths: tuple[str, ...] = ()

    @property
    def log_count(self) -> int:
        return len(self.entries)

    def to_model_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "environment": self.environment,
            "code_branch": self.code_branch,
            "from_ms": self.from_ms,
            "to_ms": self.to_ms,
            "log_count": self.log_count,
            "exception_types": list(self.exception_types),
            "truncated": self.truncated,
            "service_names": list(self.service_names),
            "endpoint_paths": list(self.endpoint_paths),
            "entries": [asdict(entry) for entry in self.entries],
        }


class GrafanaLogClient:
    def __init__(
        self,
        http_client: httpx.AsyncClient,
        *,
        url: str,
        token: str,
        targets: dict[str, GrafanaTarget],
        timeout_seconds: float = 15.0,
        max_entries: int = 20,
        max_entry_chars: int = 2000,
        max_total_chars: int = 30000,
        max_time_range_minutes: int = 60,
        app_label: str = "",
        query_max_lines: int = 1000,
    ) -> None:
        self.http_client = http_client
        self.url = url.strip()
        self.token = token.strip()
        self.targets = dict(targets)
        self.timeout_seconds = timeout_seconds
        self.max_entries = max_entries
        self.max_entry_chars = max_entry_chars
        self.max_total_chars = max_total_chars
        self.max_time_range_minutes = max_time_range_minutes
        self.app_label = app_label.strip()
        self.query_max_lines = query_max_lines
        if self.app_label and not _LABEL_VALUE_PATTERN.fullmatch(self.app_label):
            raise ValueError("app_label contains unsupported characters")
        if not 1 <= self.query_max_lines <= 5000:
            raise ValueError("query_max_lines must be between 1 and 5000")

    async def query_trace(
        self,
        trace_id: str,
        environment: str,
        time_range_minutes: int = 60,
        *,
        now_ms: int | None = None,
    ) -> TraceLogResult:
        trace_id = trace_id.strip()
        environment = environment.strip().lower()
        if not _TRACE_ID_PATTERN.fullmatch(trace_id):
            raise ValueError("trace_id contains unsupported characters")
        target = self.targets.get(environment)
        if target is None:
            raise ValueError("environment is not configured")
        if not 1 <= time_range_minutes <= self.max_time_range_minutes:
            raise ValueError(
                "time_range_minutes must be between 1 and "
                f"{self.max_time_range_minutes}"
            )

        to_ms = now_ms if now_ms is not None else int(time.time() * 1000)
        from_ms = to_ms - time_range_minutes * 60 * 1000
        payload = {
            "queries": [
                {
                    "refId": "A",
                    "expr": f'{self._selector(target)} |= "{trace_id}"',
                    "queryType": "range",
                    "maxLines": self.query_max_lines,
                    "direction": "backward",
                    "datasource": {
                        "type": "loki",
                        "uid": target.datasource_uid,
                    },
                }
            ],
            "from": str(from_ms),
            "to": str(to_ms),
        }
        authorization = self.token
        if not authorization.lower().startswith("bearer "):
            authorization = f"Bearer {authorization}"
        try:
            response = await self.http_client.post(
                self.url,
                headers={
                    "Authorization": authorization,
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=self.timeout_seconds,
            )
        except httpx.HTTPError as exc:
            raise GrafanaLogError(
                f"Grafana log query failed error_type={type(exc).__name__}",
                retryable=isinstance(exc, httpx.TransportError),
            ) from None
        if response.status_code >= 400:
            raise GrafanaLogError(
                f"Grafana log query failed status_code={response.status_code}",
                retryable=response.status_code >= 500,
                status_code=response.status_code,
            )

        entries, truncated = self._parse_entries(response.json())
        exception_types = tuple(
            dict.fromkeys(
                exception
                for entry in entries
                for exception in entry.exception_types
            )
        )
        return TraceLogResult(
            trace_id=trace_id,
            environment=environment,
            code_branch=target.code_branch,
            from_ms=from_ms,
            to_ms=to_ms,
            entries=tuple(entries),
            exception_types=exception_types,
            truncated=truncated,
            service_names=tuple(
                dict.fromkeys(entry.logger for entry in entries if entry.logger)
            ),
            endpoint_paths=tuple(
                dict.fromkeys(
                    match.group(1)
                    for entry in entries
                    for match in _ENDPOINT_PATTERN.finditer(entry.message)
                )
            ),
        )

    def _selector(self, target: GrafanaTarget) -> str:
        labels = []
        if self.app_label:
            labels.append(f'app="{self.app_label}"')
        labels.append(f'namespace="{target.namespace}"')
        return "{" + ",".join(labels) + "}"

    def _parse_entries(self, payload: Any) -> tuple[list[LogEntry], bool]:
        frames = (
            ((payload or {}).get("results") or {}).get("A", {}).get("frames")
            if isinstance(payload, dict)
            else []
        ) or []
        observed: list[LogEntry] = []
        for frame in frames:
            fields = ((frame.get("schema") or {}).get("fields") or [])
            values = ((frame.get("data") or {}).get("values") or [])
            names = [str(field.get("name") or "").lower() for field in fields]
            row_count = max((len(column) for column in values), default=0)
            for row_index in range(row_count):
                row = {
                    names[index]: column[row_index]
                    for index, column in enumerate(values)
                    if index < len(names) and row_index < len(column)
                }
                raw_line = self._line_value(row)
                if raw_line is None:
                    continue
                observed.append(self._entry(row, raw_line))

        ranked = sorted(
            enumerate(observed),
            key=lambda item: (self._level_priority(item[1].level), item[0]),
        )
        selected: list[LogEntry] = []
        total_chars = 0
        for _, entry in ranked:
            projected = total_chars + len(entry.message)
            if len(selected) >= self.max_entries or projected > self.max_total_chars:
                continue
            selected.append(entry)
            total_chars = projected
        return selected, len(selected) < len(observed)

    @staticmethod
    def _line_value(row: dict[str, Any]) -> Any | None:
        for key in ("line", "message", "body", "log"):
            if key in row:
                return row[key]
        return next(
            (value for key, value in row.items() if key not in {"time", "timestamp"}),
            None,
        )

    def _entry(self, row: dict[str, Any], raw_line: Any) -> LogEntry:
        structured: dict[str, Any] = {}
        text = str(raw_line or "")
        if isinstance(raw_line, dict):
            structured = raw_line
            text = str(
                raw_line.get("message")
                or raw_line.get("msg")
                or raw_line.get("line")
                or json.dumps(raw_line, ensure_ascii=False)
            )
        elif text.lstrip().startswith("{"):
            try:
                parsed = json.loads(text)
                if isinstance(parsed, dict):
                    structured = parsed
                    text = str(
                        parsed.get("message")
                        or parsed.get("msg")
                        or parsed.get("line")
                        or text
                    )
            except json.JSONDecodeError:
                pass

        sanitized = self._sanitize(text)[: self.max_entry_chars]
        exceptions = tuple(dict.fromkeys(_EXCEPTION_PATTERN.findall(sanitized)))
        stack_frames = tuple(
            StackFrame(
                symbol=match.group(1),
                file=match.group(2),
                line=int(match.group(3)) if match.group(3) else None,
            )
            for match in _STACK_FRAME_PATTERN.finditer(sanitized)
        )
        level = str(
            structured.get("level")
            or structured.get("severity")
            or self._infer_level(sanitized)
        ).upper()
        labels = row.get("labels") if isinstance(row.get("labels"), dict) else {}
        spring_logger = _SPRING_LOGGER_PATTERN.search(sanitized)
        logger = str(
            structured.get("logger")
            or structured.get("logger_name")
            or structured.get("service")
            or (spring_logger.group(1) if spring_logger else "")
            or labels.get("app")
            or labels.get("container")
            or ""
        )
        timestamp = str(row.get("time") or row.get("timestamp") or "")
        return LogEntry(
            timestamp=timestamp,
            level=level,
            logger=logger,
            message=sanitized,
            exception_types=exceptions,
            stack_frames=stack_frames,
        )

    @staticmethod
    def _sanitize(value: str) -> str:
        sanitized = _SECRET_PATTERN.sub(r"\1\2[REDACTED]", value)
        sanitized = _BEARER_PATTERN.sub("Bearer [REDACTED]", sanitized)
        sanitized = _EMAIL_PATTERN.sub("[REDACTED_EMAIL]", sanitized)
        return _PHONE_PATTERN.sub("[REDACTED_PHONE]", sanitized)

    @staticmethod
    def _infer_level(value: str) -> str:
        upper = value.upper()
        for level in ("ERROR", "WARN", "INFO", "DEBUG", "TRACE"):
            if level in upper:
                return level
        return "UNKNOWN"

    @staticmethod
    def _level_priority(level: str) -> int:
        return {
            "ERROR": 0,
            "WARN": 1,
            "INFO": 2,
            "DEBUG": 3,
            "TRACE": 4,
            "UNKNOWN": 5,
        }.get(level.upper(), 5)
