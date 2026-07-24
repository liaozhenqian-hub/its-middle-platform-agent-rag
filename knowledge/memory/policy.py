from __future__ import annotations

import re


class MemoryPolicy:
    _SENSITIVE = re.compile(
        r"(?:password|passwd|secret|token|api[_ -]?key|authorization|bearer|private key|"
        r"密码|口令|银行卡|身份证|手机号|访问令牌|凭证)",
        re.IGNORECASE,
    )
    _RAW_INTERNAL = re.compile(
        r"(?:^|\n)\s*(?:public|private|protected)\s+(?:class|interface|void|static)|"
        r"(?:\b(?:NullPointerException|IllegalStateException|RuntimeException|Exception)\b\s+.*?\bat\s+\S+?:\d+|"
        r"at\s+[\w.$]+\([\w./:-]+:\d+\)|stacktrace|exception\s+in\s+thread|完整日志)",
        re.IGNORECASE,
    )

    def allows_text(self, value: str) -> bool:
        text = value.strip()
        if not text or len(text) > 2000:
            return False
        if self._SENSITIVE.search(text) or self._RAW_INTERNAL.search(text):
            return False
        return True

    def allows_candidate(self, normalized_fact: str, summary: str) -> bool:
        return self.allows_text(normalized_fact) and self.allows_text(summary)
