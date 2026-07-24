from types import SimpleNamespace

import pytest

from knowledge.quality.judge import DeepSeekSemanticJudge, SemanticJudgeError


class FakeCompletions:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        content = self.outputs.pop(0)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
        )


@pytest.mark.asyncio
async def test_semantic_judge_repairs_invalid_json_once_and_validates_result():
    completions = FakeCompletions(
        [
            "not-json",
            '{"score":82,"relevance":85,"factual_correctness":82,'
            '"citation_support":80,"unknown_calibration":80,"actionability":84,'
            '"facts_supported":true,"critical_contradiction":false,"reasons":["supported"]}',
        ]
    )
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    judge = DeepSeekSemanticJudge(client=client, model="deepseek-v4-pro")

    result = await judge.judge(
        question="接口入参是什么",
        answer="代码显示入参为 id",
        evidence=[{"source_type": "code", "title": "Controller.method"}],
        required_facts=["id"],
        forbidden_facts=[],
    )

    assert result["score"] == 82
    assert len(completions.calls) == 2
    assert all(call["model"] == "deepseek-v4-pro" for call in completions.calls)


@pytest.mark.asyncio
async def test_semantic_judge_fails_closed_after_second_invalid_response():
    completions = FakeCompletions(["bad", "still bad"])
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    judge = DeepSeekSemanticJudge(client=client, model="deepseek-v4-pro")

    with pytest.raises(SemanticJudgeError):
        await judge.judge(
            question="q", answer="a", evidence=[], required_facts=[], forbidden_facts=[]
        )
