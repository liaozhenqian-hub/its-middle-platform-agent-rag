from __future__ import annotations

import json
import re
from typing import Any, Awaitable, Callable


def create_specialist_output_extractor(
    specialist_name: str,
) -> Callable[[Any], Awaitable[str]]:
    async def extract(result: Any) -> str:
        context = result.context_wrapper.context
        citations = context.public_citations(10)
        answer = str(result.final_output or "").strip()
        unknowns = [
            clause.strip()
            for clause in re.split(r"[，,；;。！？\n]+", answer)
            if clause.strip()
            and any(
                marker in clause
                for marker in ("无法确认", "未确认", "未知", "未检索到")
            )
        ]
        payload = {
            "specialist": specialist_name,
            "conclusion": answer,
            "evidence": [
                {
                    "source_type": item.source_type,
                    "source_id": item.source_id,
                    "title": item.title,
                    "domain": item.domain,
                }
                for item in citations
            ],
            "unknowns": unknowns,
            "deployment_status": "unknown",
            "confidence": 0.9 if citations else 0.0,
        }
        return json.dumps(payload, ensure_ascii=False)

    return extract
