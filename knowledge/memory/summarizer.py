from __future__ import annotations

from knowledge.memory.models import ConversationSummary
from knowledge.memory.policy import MemoryPolicy


class ConversationSummarizer:
    """Build a bounded operational summary that is never recalled as confirmed memory."""

    def __init__(self, max_chars: int = 2000, policy: MemoryPolicy | None = None):
        self.max_chars = max(200, min(max_chars, 8000))
        self.policy = policy or MemoryPolicy()

    def build(
        self,
        *,
        conversation_id: str,
        user_id: str,
        space_id: str,
        domain_id: str | None,
        goals: list[str],
        confirmed_facts: list[str],
        unresolved_items: list[str],
        preferences: list[str],
    ) -> ConversationSummary:
        groups = {
            "目标": self._safe_items(goals),
            "已确认事实": self._safe_items(confirmed_facts),
            "未解决事项": self._safe_items(unresolved_items),
            "显式偏好": self._safe_items(preferences),
        }
        lines = [
            f"{label}：" + "；".join(items)
            for label, items in groups.items()
            if items
        ]
        return ConversationSummary(
            conversation_id=conversation_id.strip(),
            user_id=user_id.strip(),
            space_id=space_id.strip(),
            domain_id=domain_id,
            summary="\n".join(lines)[: self.max_chars],
            goals=groups["目标"],
            confirmed_facts=groups["已确认事实"],
            unresolved_items=groups["未解决事项"],
            preferences=groups["显式偏好"],
        )

    def _safe_items(self, values: list[str]) -> tuple[str, ...]:
        output: list[str] = []
        for value in values[:20]:
            normalized = " ".join(str(value).split())[:500]
            if normalized and self.policy.allows_text(normalized):
                output.append(normalized)
        return tuple(output)


class ConversationSummaryService:
    """Persist a bounded, policy-filtered summary outside the agent session."""

    def __init__(self, repository, *, max_chars: int = 2000, policy: MemoryPolicy | None = None):
        self.repository = repository
        self.summarizer = ConversationSummarizer(max_chars=max_chars, policy=policy)

    async def update_from_turn(
        self,
        *,
        conversation_id: str,
        user_id: str,
        space_id: str,
        domain_id: str | None,
        question: str,
        answer: str | None,
    ) -> ConversationSummary:
        previous = await self.repository.get_conversation_summary(conversation_id)
        safe_question = self._safe_text(question)
        safe_answer = self._safe_text(answer or "")
        recent_lines = [f"最近问题：{safe_question}"]
        if safe_answer:
            recent_lines.append(f"最近结论：{safe_answer}")
        prior = previous.summary if previous else ""
        summary = "\n".join(item for item in (prior, *recent_lines) if item)
        summary = summary[-self.summarizer.max_chars:]
        values = self.summarizer.build(
            conversation_id=conversation_id,
            user_id=user_id,
            space_id=space_id,
            domain_id=domain_id,
            goals=list(previous.goals if previous else ()),
            confirmed_facts=list(previous.confirmed_facts if previous else ()),
            unresolved_items=list(previous.unresolved_items if previous else ()),
            preferences=list(previous.preferences if previous else ()),
        )
        values = ConversationSummary(
            conversation_id=values.conversation_id,
            user_id=values.user_id,
            space_id=values.space_id,
            domain_id=values.domain_id,
            summary=summary,
            goals=values.goals,
            confirmed_facts=values.confirmed_facts,
            unresolved_items=values.unresolved_items,
            preferences=values.preferences,
        )
        return await self.repository.upsert_conversation_summary(values)

    def _safe_text(self, value: str) -> str:
        normalized = " ".join(value.split())[:900]
        return normalized if self.summarizer.policy.allows_text(normalized) else ""
