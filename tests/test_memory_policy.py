import pytest

from knowledge.memory.policy import MemoryPolicy


@pytest.mark.parametrize(
    "text",
    [
        "Authorization: Bearer abc123",
        "用户密码是 abc123",
        "请记住银行卡密码 123456",
        "OPENAI_API_KEY=secret",
        "完整日志 NullPointerException at Service.java:42",
        "public void transfer() { return; }",
    ],
)
def test_memory_policy_rejects_sensitive_or_raw_internal_content(text):
    assert MemoryPolicy().allows_text(text) is False


def test_memory_policy_allows_compact_preference_and_limits_length():
    policy = MemoryPolicy()
    assert policy.allows_text("用户偏好：接口回答包含入参、出参和代码引用") is True
    assert policy.allows_text("x" * 5000) is False
