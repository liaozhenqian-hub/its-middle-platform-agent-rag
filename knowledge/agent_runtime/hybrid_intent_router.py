from __future__ import annotations

import asyncio
import logging
from time import perf_counter
from typing import Any

from knowledge.agent_runtime.intent_router import DomainIntentRouter, RoutingDecision


logger = logging.getLogger(__name__)


class HybridDomainIntentRouter:
    """Use deterministic rules first and Flash only for unresolved messages."""

    _DOMAIN_IDS = {
        "指标平台": "metric-platform",
        "审批流": "approval-flow",
        "工作流": "workflow",
        "metric-platform": "metric-platform",
        "approval-flow": "approval-flow",
        "workflow": "workflow",
    }
    _TASK_TYPES = {
        "unknown",
        "how_to",
        "api_contract",
        "code_lookup",
        "requirement_analysis",
        "metric_query",
        "bug",
    }

    def __init__(self, rules: DomainIntentRouter, fallback: Any | None):
        self.rules = rules
        self.fallback = fallback

    def route_rules(self, message: str) -> RoutingDecision:
        return self.rules.route(message)

    async def route(self, message: str) -> RoutingDecision:
        primary = self.rules.route(message)
        if primary.domains or primary.intent == "bug" or self.fallback is None:
            return primary

        started = perf_counter()
        try:
            rewritten = await asyncio.to_thread(
                self.fallback.rewrite, message, "middle-platform"
            )
        except Exception as exc:
            logger.warning(
                "Flash intent fallback unavailable error_type=%s", type(exc).__name__
            )
            return primary

        domains = tuple(
            dict.fromkeys(
                self._DOMAIN_IDS[value]
                for value in rewritten.domain_candidates
                if value in self._DOMAIN_IDS
            )
        )
        if not domains:
            return primary
        task_type = str(getattr(rewritten, "task_type", "unknown") or "unknown")
        if task_type not in self._TASK_TYPES:
            task_type = primary.task_type
        return RoutingDecision(
            domains=domains,
            intent=domains[0] if len(domains) == 1 else "cross-domain",
            confidence=0.85,
            needs_clarification=bool(rewritten.clarification_needed),
            reason_codes=("flash_domain_fallback",),
            task_type=task_type if task_type != "unknown" else primary.task_type,
            route_source="flash_fallback",
            duration_ms=(perf_counter() - started) * 1000,
        )
