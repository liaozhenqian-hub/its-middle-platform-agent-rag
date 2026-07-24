import json

import pytest

from knowledge.memory.extractor import MemoryExtractor


class FakeCompletions:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return type("Response", (), {
            "choices": [type("Choice", (), {
                "message": type("Message", (), {"content": self.responses.pop(0)})()
            })()]
        })()


class FakeClient:
    def __init__(self, responses):
        self.chat = type("Chat", (), {"completions": FakeCompletions(responses)})()


@pytest.mark.asyncio
async def test_memory_extractor_validates_json_and_repairs_once():
    client = FakeClient([
        "not-json",
        json.dumps({
            "memories": [{
                "memory_type": "user_preference",
                "scope_type": "user",
                "subject": "format",
                "normalized_fact": "回答接口问题包含入参和出参",
                "summary": "用户偏好接口回答包含入参和出参",
                "confidence": 0.91,
            }]
        }, ensure_ascii=False),
    ])
    extractor = MemoryExtractor(client=client, model="deepseek-v4-flash")

    result = await extractor.extract("用户希望接口回答包含入参和出参", "答案", "审批流")

    assert len(result) == 1
    assert result[0].subject == "format"
    assert len(client.chat.completions.calls) == 2


@pytest.mark.asyncio
async def test_memory_extractor_drops_sensitive_candidate_after_validation():
    client = FakeClient([json.dumps({
        "memories": [{
            "memory_type": "user_context",
            "scope_type": "user",
            "subject": "token",
            "normalized_fact": "Authorization: Bearer abc",
            "summary": "用户 token",
            "confidence": 0.99,
        }]
    })])
    result = await MemoryExtractor(client=client, model="flash").extract(
        "我的 token 是 abc", "答案", "指标平台"
    )
    assert result == []
