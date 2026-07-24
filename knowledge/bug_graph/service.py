from __future__ import annotations

import asyncio
import logging
from contextlib import ExitStack
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from time import perf_counter
from typing import Any, Awaitable, Callable, Protocol

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from knowledge.bug_graph.intake import BugIntakeParser
from knowledge.bug_graph.models import BugDiagnosisState, EvidenceGrade
from knowledge.logs.grafana import GrafanaLogError, TraceLogResult


logger = logging.getLogger(__name__)


class TraceLogClient(Protocol):
    async def query_trace(
        self,
        trace_id: str,
        environment: str,
        time_range_minutes: int,
        *,
        now_ms: int | None = None,
    ) -> TraceLogResult: ...


class CodeRetriever(Protocol):
    async def search(
        self,
        state: BugDiagnosisState,
        log_result: TraceLogResult,
    ) -> list[dict[str, Any]]: ...


class DiagnosisGenerator(Protocol):
    async def generate(
        self,
        state: BugDiagnosisState,
        evidence: dict[str, Any],
    ) -> str: ...


class EvidenceEnricher(Protocol):
    async def enrich(
        self,
        state: BugDiagnosisState,
        code_matches: list[dict[str, Any]],
    ) -> dict[str, Any]: ...


class ConversationContextResolver(Protocol):
    async def get_latest_bug_context(
        self,
        conversation_id: str,
    ) -> dict[str, str | None] | None: ...


class IncidentMemoryRecorder(Protocol):
    async def record(
        self, user_id: str | None, state: dict[str, Any], result: "BugDiagnosisResult"
    ) -> Any: ...


@dataclass(frozen=True)
class BugDiagnosisResult:
    status: str
    answer: str
    missing_fields: list[str]
    citations: list[dict[str, Any]]
    evidence_grade: EvidenceGrade = "none"
    warnings: list[str] | None = None


class BugDiagnosisGraphService:
    _FOLLOW_UP_MARKERS = (
        "刚才",
        "上次",
        "上一轮",
        "上一条",
        "前面",
        "上面",
        "之前",
        "一开始",
        "上下文",
        "继续",
        "再查",
        "重新查",
        "为什么没",
        "为什么没有",
    )
    _REUSABLE_CONTEXT_STATUSES = {"completed", "no_logs", "unavailable"}
    _PUBLIC_SOURCE_METADATA = {
        "source_id",
        "source_type",
        "domain_id",
        "branch",
        "commit_sha",
        "relative_path",
        "language",
        "symbol_type",
        "symbol_name",
        "start_line",
        "end_line",
        "gitlab_url",
        "source_version",
        "page_number",
        "heading",
    }
    def __init__(
        self,
        *,
        checkpointer: Any,
        log_client: TraceLogClient,
        code_retriever: CodeRetriever,
        intake_parser: BugIntakeParser | None = None,
        diagnosis_generator: DiagnosisGenerator | None = None,
        evidence_enricher: EvidenceEnricher | None = None,
        context_resolver: ConversationContextResolver | None = None,
        incident_recorder: IncidentMemoryRecorder | None = None,
        entity_memory_repository: Any | None = None,
        entity_recall_limit: int = 5,
        procedural_memory_service: Any | None = None,
        procedural_guidance_enabled: bool = False,
        procedural_observe_only: bool = True,
        procedural_recall_limit: int = 3,
        interrupt_ttl_seconds: int = 86400,
        log_retry_count: int = 2,
        log_range_minutes: int = 1440,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.checkpointer = checkpointer
        self.log_client = log_client
        self.code_retriever = code_retriever
        self.intake_parser = intake_parser or BugIntakeParser()
        self.diagnosis_generator = diagnosis_generator
        self.evidence_enricher = evidence_enricher
        self.context_resolver = context_resolver
        self.incident_recorder = incident_recorder
        self.entity_memory_repository = entity_memory_repository
        self.entity_recall_limit = max(1, min(entity_recall_limit, 20))
        self.procedural_memory_service = procedural_memory_service
        self.procedural_guidance_enabled = procedural_guidance_enabled
        self.procedural_observe_only = procedural_observe_only
        self.procedural_recall_limit = max(1, min(procedural_recall_limit, 10))
        self.interrupt_ttl_seconds = interrupt_ttl_seconds
        self.log_retry_count = log_retry_count
        self.log_range_minutes = log_range_minutes
        self.now = now or (lambda: datetime.now(UTC))
        self._evidence: dict[str, dict[str, Any]] = {}
        self._run_users: dict[str, str] = {}
        self._diagnosis_delta_handlers: dict[
            str, Callable[[str], Awaitable[None] | None]
        ] = {}
        self.graph = self._build_graph()

    def _build_graph(self):
        builder = StateGraph(BugDiagnosisState)
        builder.add_node("understand_input", self._audited("understand_input", self._understand_input))
        builder.add_node("validate_required_fields", self._audited("validate_required_fields", self._validate_required_fields))
        builder.add_node("select_procedure", self._audited("select_procedure", self._select_procedure))
        builder.add_node("interrupt_for_clarification", self._audited("interrupt_for_clarification", self._interrupt_for_clarification))
        builder.add_node("query_trace_logs", self._audited("query_trace_logs", self._query_trace_logs))
        builder.add_node("extract_log_signals", self._audited("extract_log_signals", self._extract_log_signals))
        builder.add_node("search_branch_code", self._audited("search_branch_code", self._search_branch_code))
        builder.add_node("enrich_code_context", self._audited("enrich_code_context", self._enrich_code_context))
        builder.add_node("inspect_contract_and_docs", self._audited("inspect_contract_and_docs", self._inspect_contract_and_docs))
        builder.add_node("grade_evidence", self._audited("grade_evidence", self._grade_evidence))
        builder.add_node("generate_diagnosis", self._audited("generate_diagnosis", self._generate_diagnosis))
        builder.add_node("finalize", self._audited("finalize", self._finalize))
        builder.add_edge(START, "understand_input")
        builder.add_edge("understand_input", "validate_required_fields")
        builder.add_conditional_edges(
            "validate_required_fields",
            lambda state: "interrupt" if state.get("missing_fields") else "query",
            {
                "interrupt": "interrupt_for_clarification",
                "query": "select_procedure",
            },
        )
        builder.add_edge("interrupt_for_clarification", "understand_input")
        builder.add_edge("select_procedure", "query_trace_logs")
        builder.add_conditional_edges(
            "query_trace_logs",
            lambda state: (
                "finish"
                if state.get("status") in {"no_logs", "unavailable"}
                else "extract"
            ),
            {"finish": "finalize", "extract": "extract_log_signals"},
        )
        builder.add_edge("extract_log_signals", "search_branch_code")
        builder.add_edge("search_branch_code", "enrich_code_context")
        builder.add_edge("enrich_code_context", "inspect_contract_and_docs")
        builder.add_edge("inspect_contract_and_docs", "grade_evidence")
        builder.add_edge("grade_evidence", "generate_diagnosis")
        builder.add_edge("generate_diagnosis", "finalize")
        builder.add_edge("finalize", END)
        return builder.compile(checkpointer=self.checkpointer, name="bug-diagnosis")

    async def start(self) -> None:
        setup = getattr(self.checkpointer, "setup", None)
        if callable(setup):
            await setup()

    async def close(self) -> None:
        self._evidence.clear()
        self._run_users.clear()
        self._diagnosis_delta_handlers.clear()

    async def diagnose(
        self,
        bug_report: str,
        *,
        conversation_id: str,
        run_id: str,
        user_id: str | None = None,
        on_diagnosis_delta: Callable[[str], Awaitable[None] | None] | None = None,
    ) -> BugDiagnosisResult:
        if user_id:
            self._run_users[run_id] = user_id
        config = {"configurable": {"thread_id": conversation_id}}
        if bug_report.strip() == "取消诊断":
            await self.cancel(conversation_id)
            return BugDiagnosisResult(
                status="cancelled",
                answer="已取消当前 Bug 诊断。",
                missing_fields=[],
                citations=[],
            )

        snapshot = await self.graph.aget_state(config)
        snapshot_values = dict(snapshot.values or {})
        if snapshot_values and self._expired(snapshot_values):
            await self.checkpointer.adelete_thread(conversation_id)
            snapshot_values = {}
        pending = snapshot_values.get("status") == "clarification_required"
        inherited_context = await self._resolve_reusable_context(
            conversation_id,
            bug_report,
            snapshot_values,
        )
        if on_diagnosis_delta is not None:
            self._diagnosis_delta_handlers[run_id] = on_diagnosis_delta

        try:
            with self._bind_models(conversation_id):
                if pending:
                    output = await self.graph.ainvoke(
                        Command(
                            resume={
                                "message": bug_report,
                                "run_id": run_id,
                                "inherited_context": inherited_context,
                            }
                        ),
                        config,
                    )
                else:
                    output = await self.graph.ainvoke(
                        self._initial_state(
                            bug_report,
                            conversation_id=conversation_id,
                            run_id=run_id,
                            inherited_context=inherited_context,
                        ),
                        config,
                    )
        except Exception:
            self._evidence.pop(run_id, None)
            raise
        finally:
            self._diagnosis_delta_handlers.pop(run_id, None)

        result = self._to_result(output)
        if self.incident_recorder is not None and user_id:
            try:
                await self.incident_recorder.record(user_id, dict(output), result)
            except Exception as exc:
                logger.warning(
                    "Bug incident memory candidate failed error_type=%s",
                    type(exc).__name__,
                )
        if result.status != "clarification_required":
            self._evidence.pop(run_id, None)
        self._run_users.pop(run_id, None)
        self._run_users.pop(str(output.get("run_id") or run_id), None)
        return result

    async def has_pending(self, conversation_id: str) -> bool:
        config = {"configurable": {"thread_id": conversation_id}}
        snapshot = await self.graph.aget_state(config)
        pending = bool(
            snapshot.values
            and snapshot.values.get("status") == "clarification_required"
        )
        if pending and self._expired(snapshot.values):
            await self.checkpointer.adelete_thread(conversation_id)
            return False
        return pending

    async def should_resume(self, conversation_id: str, message: str) -> bool:
        if not self._is_follow_up(message):
            return False
        config = {"configurable": {"thread_id": conversation_id}}
        snapshot = await self.graph.aget_state(config)
        values = dict(snapshot.values or {})
        if values and not self._expired(values):
            if values.get("environment") and values.get("trace_id"):
                return True
        if self.context_resolver is None:
            return False
        return bool(
            await self.context_resolver.get_latest_bug_context(conversation_id)
        )

    async def _resolve_reusable_context(
        self,
        conversation_id: str,
        message: str,
        snapshot_values: dict[str, Any],
    ) -> dict[str, str | None] | None:
        if not self._is_follow_up(message):
            return None
        context: dict[str, str | None] = {
            "environment": snapshot_values.get("environment"),
            "trace_id": snapshot_values.get("trace_id"),
            "request_time": snapshot_values.get("request_time"),
        }
        if (
            (not context["environment"] or not context["trace_id"])
            and self.context_resolver is not None
        ):
            historical = await self.context_resolver.get_latest_bug_context(
                conversation_id
            )
            if historical:
                for key in ("environment", "trace_id", "request_time"):
                    if not context.get(key) and historical.get(key):
                        context[key] = historical[key]
        if not context["environment"] or not context["trace_id"]:
            return None
        return context

    def _initial_state(
        self,
        bug_report: str,
        *,
        conversation_id: str,
        run_id: str,
        inherited_context: dict[str, str | None] | None,
    ) -> dict[str, Any]:
        now = self._utc_now()
        inherited = inherited_context or {}
        return {
            "conversation_id": conversation_id,
            "run_id": run_id,
            "original_message": bug_report.strip(),
            "latest_message": bug_report.strip(),
            "normalized_problem": bug_report.strip(),
            "environment": inherited.get("environment"),
            "environment_evidence": "",
            "trace_id": inherited.get("trace_id"),
            "service": None,
            "endpoint": None,
            "request_time": inherited.get("request_time"),
            "request_time_evidence": "",
            "symptoms": [],
            "domain_hints": [],
            "missing_fields": [],
            "clarification_question": "",
            "status": "running",
            "current_stage": "understand_input",
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
            "interrupted_at": None,
            "expires_at": (
                now + timedelta(seconds=self.interrupt_ttl_seconds)
            ).isoformat(),
            "log_count": 0,
            "exception_types": [],
            "stack_frames": [],
            "logs_truncated": False,
            "log_services": [],
            "log_endpoints": [],
            "code_chunk_ids": [],
            "code_matches": [],
            "swagger_operations": [],
            "document_chunk_ids": [],
            "entity_hints": [],
            "selected_procedure_id": None,
            "selected_procedure_version": None,
            "procedure_capabilities": [],
            "procedure_observe_only": self.procedural_observe_only,
            "citations": [],
            "evidence_grade": "none",
            "warnings": [],
            "terminal_reason": None,
            "answer": "",
        }

    @classmethod
    def _is_follow_up(cls, message: str) -> bool:
        normalized = message.strip().casefold()
        return any(marker in normalized for marker in cls._FOLLOW_UP_MARKERS)

    async def cancel(self, conversation_id: str) -> None:
        await self.checkpointer.adelete_thread(conversation_id)

    async def _understand_input(self, state: BugDiagnosisState) -> dict[str, Any]:
        intake = await self.intake_parser.parse(
            state.get("latest_message") or state["original_message"],
            normalize=False,
            allow_standalone_trace=(state.get("missing_fields") == ["trace_id"]),
        )
        environment = intake.environment or state.get("environment")
        trace_id = intake.trace_id or state.get("trace_id")
        missing_fields = []
        if environment is None:
            missing_fields.append("environment")
        if trace_id is None:
            missing_fields.append("trace_id")
        values = intake.model_dump(
            exclude={
                "original_message",
                "environment",
                "trace_id",
                "missing_fields",
                "clarification_question",
            }
        )
        for field in (
            "service",
            "endpoint",
            "request_time",
            "request_time_evidence",
        ):
            if not values.get(field) and state.get(field):
                values[field] = state[field]
        values["normalized_problem"] = state.get(
            "normalized_problem",
            state["original_message"],
        )
        return {
            **values,
            "environment": environment,
            "trace_id": trace_id,
            "missing_fields": missing_fields,
            "clarification_question": self.intake_parser._clarification_question(
                missing_fields
            ),
            "status": "running",
            "current_stage": "understand_input",
            "updated_at": self._utc_now().isoformat(),
        }

    async def _validate_required_fields(self, state: BugDiagnosisState) -> dict[str, Any]:
        if state.get("missing_fields"):
            now = self._utc_now()
            return {
                "status": "clarification_required",
                "current_stage": "interrupt_for_clarification",
                "interrupted_at": now.isoformat(),
                "expires_at": (
                    now + timedelta(seconds=self.interrupt_ttl_seconds)
                ).isoformat(),
            }
        return {"current_stage": "select_procedure"}

    async def _select_procedure(self, state: BugDiagnosisState) -> dict[str, Any]:
        fallback = {"current_stage": "select_procedure"}
        service = getattr(self, "procedural_memory_service", None)
        if not getattr(self, "procedural_guidance_enabled", False) or service is None:
            return fallback
        run_id = str(state.get("run_id") or "")
        user_id = getattr(self, "_run_users", {}).get(run_id)
        environment = str(state.get("environment") or "")
        if not user_id or environment not in {"develop", "test", "prod"}:
            return fallback
        domain_hints = list(state.get("domain_hints") or [])
        domain_id = str(domain_hints[0]) if len(domain_hints) == 1 else None
        branch = "master" if environment == "prod" else "develop"
        try:
            matches = await service.recall_procedures(
                user_id=user_id, domain_id=domain_id, task_type="bug_diagnosis",
                environment=environment, branch=branch,
                limit=getattr(self, "procedural_recall_limit", 3),
            )
        except Exception:
            return fallback
        if not matches:
            return fallback
        memory, spec = matches[0]
        return {
            "selected_procedure_id": str(memory.id),
            "selected_procedure_version": int(spec.procedure_version),
            "procedure_capabilities": list(spec.allowed_tools),
            "procedure_observe_only": bool(getattr(self, "procedural_observe_only", True)),
            "current_stage": "select_procedure",
        }

    async def _interrupt_for_clarification(self, state: BugDiagnosisState) -> dict[str, Any]:
        resumed = interrupt(
            {
                "missing_fields": state.get("missing_fields", []),
                "question": state.get("clarification_question", "请补充诊断信息。"),
            }
        )
        message = str((resumed or {}).get("message") or "").strip()
        inherited = (resumed or {}).get("inherited_context") or {}
        combined = f"{state['original_message']}\n补充信息：{message}".strip()
        return {
            "run_id": str((resumed or {}).get("run_id") or state["run_id"]),
            "original_message": combined,
            "latest_message": message,
            "environment": inherited.get("environment") or state.get("environment"),
            "trace_id": inherited.get("trace_id") or state.get("trace_id"),
            "request_time": inherited.get("request_time") or state.get("request_time"),
            "status": "running",
            "current_stage": "understand_input",
        }

    async def _query_trace_logs(self, state: BugDiagnosisState) -> dict[str, Any]:
        query_range_minutes = self.log_range_minutes
        query_now_ms = None
        if state.get("request_time"):
            query_range_minutes = min(60, self.log_range_minutes)
            request_time = datetime.fromisoformat(str(state["request_time"]))
            query_end = request_time + timedelta(
                minutes=query_range_minutes / 2
            )
            query_now_ms = int(query_end.timestamp() * 1000)
        result = None
        last_error: Exception | None = None
        for attempt in range(self.log_retry_count + 1):
            try:
                result = await self.log_client.query_trace(
                    str(state["trace_id"]),
                    str(state["environment"]),
                    query_range_minutes,
                    now_ms=query_now_ms,
                )
                break
            except Exception as exc:
                last_error = exc
                if attempt >= self.log_retry_count or not self._retryable_log_error(exc):
                    break
                await asyncio.sleep(0)
        if result is None:
            logger.info(
                "Bug log query completed environment=%s targeted=%s "
                "range_minutes=%d status=unavailable",
                state.get("environment"),
                query_now_ms is not None,
                query_range_minutes,
            )
            return {
                "status": "unavailable",
                "terminal_reason": type(last_error).__name__ if last_error else "unknown",
                "answer": "日志平台暂时不可用，当前无法完成 Bug 诊断。",
                "current_stage": "finalize",
            }
        self._evidence.setdefault(state["run_id"], {})["logs"] = result
        citation = self._log_citation(result)
        logger.info(
            "Bug log query completed environment=%s targeted=%s "
            "range_minutes=%d status=available log_count=%d truncated=%s",
            state.get("environment"),
            query_now_ms is not None,
            query_range_minutes,
            result.log_count,
            result.truncated,
        )
        if result.log_count == 0:
            return {
                "status": "no_logs",
                "terminal_reason": "trace_query_returned_no_logs",
                "log_count": 0,
                "citations": [citation],
                "evidence_grade": "none",
                "answer": (
                    "最近 24 小时内没有查询到该 trace ID 的日志，请确认环境和 trace ID 是否正确。"
                ),
                "current_stage": "finalize",
            }
        return {
            "status": "running",
            "log_count": result.log_count,
            "logs_truncated": result.truncated,
            "citations": [citation],
            "current_stage": "extract_log_signals",
        }

    async def _extract_log_signals(self, state: BugDiagnosisState) -> dict[str, Any]:
        logs: TraceLogResult = self._evidence[state["run_id"]]["logs"]
        frames = [
            asdict(frame)
            for entry in logs.entries
            for frame in entry.stack_frames
        ]
        return {
            "exception_types": list(logs.exception_types),
            "log_services": list(logs.service_names),
            "log_endpoints": list(logs.endpoint_paths),
            "stack_frames": list({(item["symbol"], item["file"], item["line"]): item for item in frames}.values()),
            "current_stage": "search_branch_code",
        }

    async def _search_branch_code(self, state: BugDiagnosisState) -> dict[str, Any]:
        logs: TraceLogResult = self._evidence[state["run_id"]]["logs"]
        entity_hints: list[str] = []
        user_id = self._run_users.get(state["run_id"])
        if self.entity_memory_repository is not None and user_id:
            query = " ".join(
                str(item)
                for item in (
                    state.get("normalized_problem"),
                    state.get("service"),
                    state.get("endpoint"),
                    *state.get("log_services", []),
                    *state.get("log_endpoints", []),
                )
                if item
            )
            if query:
                try:
                    relations = await self.entity_memory_repository.search(
                        query,
                        scope_type="user",
                        owner_id=user_id,
                        space_id="middle-platform",
                        domain_id=next(iter(state.get("domain_hints", [])), None),
                        branch=logs.code_branch,
                        environment=state.get("environment"),
                        limit=self.entity_recall_limit,
                    )
                    entity_hints = list(dict.fromkeys(
                        value
                        for item in relations
                        for value in (item.source_name, item.target_name, item.summary)
                        if value
                    ))[:15]
                except Exception as exc:
                    logger.warning(
                        "Bug entity memory lookup failed error_type=%s",
                        type(exc).__name__,
                    )
        retrieval_state = dict(state)
        retrieval_state["entity_hints"] = entity_hints
        matches = await self.code_retriever.search(retrieval_state, logs)
        self._evidence.setdefault(state["run_id"], {})["code"] = matches
        citations = list(state.get("citations", []))
        citations.extend(self._code_citation(item) for item in matches)
        return {
            "code_chunk_ids": [str(item["chunk_id"]) for item in matches],
            "code_matches": [
                {
                    "chunk_id": item["chunk_id"],
                    "heading": item.get("heading", ""),
                    "domain": item.get("domain", ""),
                    "metadata": self._public_source_metadata(
                        item.get("metadata") or {}
                    ),
                }
                for item in matches
            ],
            "citations": citations,
            "entity_hints": entity_hints,
            "current_stage": "enrich_code_context",
        }

    async def _enrich_code_context(self, state: BugDiagnosisState) -> dict[str, Any]:
        enrich = getattr(self.code_retriever, "enrich", None)
        if callable(enrich):
            current = list(self._evidence.get(state["run_id"], {}).get("code", []))
            enriched = await enrich(state, current)
            if enriched:
                self._evidence.setdefault(state["run_id"], {})["code"] = enriched
        return {"current_stage": "inspect_contract_and_docs"}

    async def _inspect_contract_and_docs(self, state: BugDiagnosisState) -> dict[str, Any]:
        if self.evidence_enricher is None or not state.get("code_chunk_ids"):
            return {"current_stage": "grade_evidence"}
        current = list(self._evidence.get(state["run_id"], {}).get("code", []))
        try:
            evidence = await self.evidence_enricher.enrich(state, current)
        except Exception:
            warnings = list(state.get("warnings") or [])
            warnings.append("contract_evidence_unavailable")
            return {
                "warnings": warnings,
                "current_stage": "grade_evidence",
            }

        swagger_operations = list(evidence.get("swagger_operations") or [])
        document_matches = list(evidence.get("document_matches") or [])
        self._evidence.setdefault(state["run_id"], {})["contracts"] = {
            "swagger_operations": swagger_operations,
            "documents": document_matches,
        }
        citations = list(state.get("citations") or [])
        citations.extend(self._document_citation(item) for item in document_matches)
        citations.extend(self._swagger_citation(item) for item in swagger_operations)
        return {
            "swagger_operations": [self._public_swagger(item) for item in swagger_operations],
            "document_chunk_ids": [
                str(item["chunk_id"]) for item in document_matches if item.get("chunk_id")
            ],
            "citations": citations,
            "current_stage": "grade_evidence",
        }

    async def _grade_evidence(self, state: BugDiagnosisState) -> dict[str, Any]:
        if state.get("swagger_operations") or state.get("document_chunk_ids"):
            grade: EvidenceGrade = "contract_supported"
        elif state.get("code_chunk_ids"):
            grade = "correlated"
        else:
            grade = "log_only"
        return {"evidence_grade": grade, "current_stage": "generate_diagnosis"}

    async def _generate_diagnosis(self, state: BugDiagnosisState) -> dict[str, Any]:
        evidence = self._model_evidence(state)
        warnings: list[str] | None = None
        if self.diagnosis_generator is not None:
            try:
                handler = self._diagnosis_delta_handlers.get(str(state.get("run_id") or ""))
                generate_stream = getattr(self.diagnosis_generator, "generate_stream", None)
                if handler is not None and callable(generate_stream):
                    answer = await generate_stream(state, evidence, handler)
                else:
                    answer = await self.diagnosis_generator.generate(state, evidence)
            except Exception as exc:
                logger.warning(
                    "Bug diagnosis model unavailable error_type=%s",
                    type(exc).__name__,
                )
                answer = self._deterministic_answer(state)
                warnings = list(state.get("warnings") or [])
                warnings.append("diagnosis_model_unavailable")
        else:
            answer = self._deterministic_answer(state)
        result = {
            "answer": answer,
            "status": "completed",
            "current_stage": "finalize",
        }
        if warnings is not None:
            result["warnings"] = warnings
        return result

    async def _finalize(self, state: BugDiagnosisState) -> dict[str, Any]:
        return {"updated_at": self._utc_now().isoformat(), "current_stage": "completed"}

    def _model_evidence(self, state: BugDiagnosisState) -> dict[str, Any]:
        evidence = self._evidence.get(state["run_id"], {})
        logs: TraceLogResult | None = evidence.get("logs")
        return {
            "logs": [
                self._bounded_value(asdict(entry), max_string_chars=2000)
                for entry in (logs.entries[:20] if logs else [])
            ],
            "code": [
                self._bounded_code_evidence(item)
                for item in list(evidence.get("code", []))[:5]
            ],
            "contracts": self._bounded_contract_evidence(
                evidence.get("contracts", {})
            ),
            "evidence_grade": state.get("evidence_grade", "none"),
        }

    @staticmethod
    def _retryable_log_error(error: Exception) -> bool:
        return isinstance(error, GrafanaLogError) and bool(error.retryable)

    @staticmethod
    def _deterministic_answer(state: BugDiagnosisState) -> str:
        if state.get("evidence_grade") == "log_only":
            return "已确认日志异常，但尚未定位到足够相关的代码，暂不能确认代码根因。"
        return "已根据日志与代码证据完成诊断，请结合引用位置验证修复方案。"

    @staticmethod
    def _log_citation(result: TraceLogResult) -> dict[str, Any]:
        return {
            "source_type": "log_trace",
            "source_id": result.trace_id,
            "title": f"{result.environment} trace {result.trace_id}",
            "domain": "中台",
            "metadata": {
                "environment": result.environment,
                "from_ms": result.from_ms,
                "to_ms": result.to_ms,
                "log_count": result.log_count,
                "exception_types": list(result.exception_types),
                "truncated": result.truncated,
            },
        }

    @staticmethod
    def _code_citation(item: dict[str, Any]) -> dict[str, Any]:
        return {
            "source_type": "code",
            "source_id": str(item["chunk_id"]),
            "title": str(item.get("heading") or item["chunk_id"]),
            "domain": str(item.get("domain") or "中台"),
            "metadata": BugDiagnosisGraphService._public_source_metadata(
                item.get("metadata") or {}
            ),
        }

    @staticmethod
    def _document_citation(item: dict[str, Any]) -> dict[str, Any]:
        return {
            "source_type": "product_document",
            "source_id": str(item["chunk_id"]),
            "title": str(item.get("heading") or item["chunk_id"]),
            "domain": str(item.get("domain") or "中台"),
            "metadata": BugDiagnosisGraphService._public_source_metadata(
                item.get("metadata") or {}
            ),
        }

    @classmethod
    def _swagger_citation(cls, item: dict[str, Any]) -> dict[str, Any]:
        public = cls._public_swagger(item)
        operation_id = str(public.get("operation_id") or "").strip()
        method = str(public.get("method") or "UNKNOWN").upper()
        path = str(public.get("path") or "/unknown")
        source_id = str(public.pop("source_id", ""))
        identity = operation_id or f"{method}:{path}"
        return {
            "source_type": "swagger",
            "source_id": f"{source_id}:{identity}",
            "title": operation_id or f"{method} {path}",
            "domain": str(item.get("domain") or "中台"),
            "metadata": public,
        }

    @staticmethod
    def _public_swagger(item: dict[str, Any]) -> dict[str, Any]:
        allowed = {
            "source_id",
            "operation_id",
            "method",
            "path",
            "refreshed_at",
            "stale",
        }
        return {key: value for key, value in item.items() if key in allowed}

    @staticmethod
    def _public_source_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
        return {
            key: value
            for key, value in metadata.items()
            if key in BugDiagnosisGraphService._PUBLIC_SOURCE_METADATA
            and isinstance(value, (str, int, float, bool, type(None)))
        }

    @classmethod
    def _bounded_code_evidence(cls, item: dict[str, Any]) -> dict[str, Any]:
        bounded = {
            "chunk_id": str(item.get("chunk_id") or ""),
            "heading": str(item.get("heading") or "")[:1000],
            "content": str(item.get("content") or "")[:6000],
            "domain": str(item.get("domain") or "")[:200],
            "metadata": cls._public_source_metadata(item.get("metadata") or {}),
            "match_type": str(item.get("match_type") or "")[:100],
            "rerank_score": item.get("rerank_score"),
            "structural_context": cls._bounded_value(
                item.get("structural_context") or {},
                max_string_chars=2000,
            ),
            "context_chunks": [],
        }
        bounded["context_chunks"] = [
            {
                "chunk_id": str(context.get("chunk_id") or ""),
                "heading": str(context.get("heading") or "")[:1000],
                "content": str(context.get("content") or "")[:3000],
                "metadata": cls._public_source_metadata(
                    context.get("metadata") or {}
                ),
            }
            for context in list(item.get("context_chunks") or [])[:2]
        ]
        return bounded

    @classmethod
    def _bounded_contract_evidence(cls, value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            return {}
        documents = [
            {
                "chunk_id": str(item.get("chunk_id") or ""),
                "heading": str(item.get("heading") or "")[:1000],
                "content": str(item.get("content") or "")[:4000],
                "domain": str(item.get("domain") or "")[:200],
                "metadata": cls._public_source_metadata(item.get("metadata") or {}),
            }
            for item in list(value.get("documents") or [])[:3]
        ]
        swagger = cls._bounded_value(
            list(value.get("swagger_operations") or [])[:3],
            max_string_chars=2000,
        )
        return {"documents": documents, "swagger_operations": swagger}

    @classmethod
    def _bounded_value(
        cls,
        value: Any,
        *,
        max_string_chars: int,
        depth: int = 0,
    ) -> Any:
        if depth >= 5:
            return None
        if isinstance(value, str):
            return value[:max_string_chars]
        if isinstance(value, dict):
            return {
                str(key)[:200]: cls._bounded_value(
                    item,
                    max_string_chars=max_string_chars,
                    depth=depth + 1,
                )
                for key, item in list(value.items())[:30]
            }
        if isinstance(value, (list, tuple)):
            return [
                cls._bounded_value(
                    item,
                    max_string_chars=max_string_chars,
                    depth=depth + 1,
                )
                for item in list(value)[:30]
            ]
        if isinstance(value, (int, float, bool)) or value is None:
            return value
        return str(value)[:max_string_chars]

    @staticmethod
    def _to_result(output: dict[str, Any]) -> BugDiagnosisResult:
        if output.get("__interrupt__"):
            return BugDiagnosisResult(
                status="clarification_required",
                answer=str(output.get("clarification_question") or "请补充诊断信息。"),
                missing_fields=list(output.get("missing_fields") or []),
                citations=list(output.get("citations") or []),
                evidence_grade=output.get("evidence_grade", "none"),
                warnings=list(output.get("warnings") or []),
            )
        return BugDiagnosisResult(
            status=str(output.get("status") or "completed"),
            answer=str(output.get("answer") or ""),
            missing_fields=list(output.get("missing_fields") or []),
            citations=list(output.get("citations") or []),
            evidence_grade=output.get("evidence_grade", "none"),
            warnings=list(output.get("warnings") or []),
        )

    def _expired(self, state: dict[str, Any]) -> bool:
        value = state.get("expires_at")
        if not value:
            return True
        return datetime.fromisoformat(str(value)) <= self._utc_now()

    def _utc_now(self) -> datetime:
        value = self.now()
        return value if value.tzinfo else value.replace(tzinfo=UTC)

    def _bind_models(self, conversation_id: str) -> ExitStack:
        stack = ExitStack()
        seen: set[int] = set()
        for target in (self.intake_parser.normalizer, self.diagnosis_generator):
            if target is None or id(target) in seen:
                continue
            seen.add(id(target))
            binder = getattr(target, "bind_conversation", None)
            if callable(binder):
                stack.enter_context(binder(conversation_id))
        return stack

    @staticmethod
    def _audited(name: str, handler: Any):
        async def audited_node(state: BugDiagnosisState) -> dict[str, Any]:
            started_at = perf_counter()
            status = "completed"
            try:
                return await handler(state)
            except BaseException as exc:
                status = (
                    "interrupted"
                    if type(exc).__name__ in {"GraphInterrupt", "GraphBubbleUp"}
                    else "failed"
                )
                raise
            finally:
                logger.info(
                    "Bug graph node=%s status=%s duration_ms=%.2f",
                    name,
                    status,
                    (perf_counter() - started_at) * 1000,
                )

        return audited_node
