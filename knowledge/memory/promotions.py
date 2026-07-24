from __future__ import annotations

import re

from knowledge.memory.models import Memory
from knowledge.memory.policy import MemoryPolicy


class DomainPromotionValidator:
    _PRIVATE = re.compile(r"\banon:[\w-]+|\bou_[\w-]+|trace\s*id\s*[:=]?\s*[\w-]+", re.I)

    def __init__(self, policy: MemoryPolicy | None = None):
        self.policy = policy or MemoryPolicy()

    def validate(self, memory: Memory, target_domain_id: str, public_summary: str) -> str:
        summary = " ".join(public_summary.split())[:1000]
        if (
            memory.scope_type != "user"
            or memory.memory_type not in {"episodic_memory", "procedural_memory"}
            or not memory.source_citations
            or not target_domain_id.strip()
            or not summary
            or self._PRIVATE.search(summary)
            or not self.policy.allows_text(summary)
        ):
            raise ValueError("memory is not eligible for domain promotion")
        return summary
