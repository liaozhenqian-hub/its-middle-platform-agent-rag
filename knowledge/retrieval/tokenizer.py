import re

import jieba


TECHNICAL_TOKEN_RE = re.compile(
    r"https?://[^\s，,；;。！？!?]+"
    r"|/[A-Za-z0-9._~!$&'()*+;=:@%/-]+"
    r"|[A-Za-z][A-Za-z0-9_.-]*(?:/[A-Za-z0-9_.-]+)+"
    r"|[A-Za-z][A-Za-z0-9_.-]*"
)
SEARCHABLE_CHARACTER_RE = re.compile(r"[A-Za-z0-9\u4e00-\u9fff]")
DEFAULT_STOPWORDS = frozenset(
    {
        "一下",
        "什么",
        "可以",
        "告诉",
        "如何",
        "怎么",
        "我",
        "是否",
        "的",
        "能否",
        "请问",
    }
)


class JiebaSearchTokenizer:
    """面向中文技术文档检索的分词器。

    普通中文交给 jieba 搜索模式切词；
    但接口路径、URL、类名、方法名、英文标识符会先被正则提取出来，
    避免 `MetricClient.getDataV2`、`/api/datacenter/v2/getData` 这类关键 token 被切碎。
    """

    def __init__(self, stopwords: set[str] | frozenset[str] | None = None):
        self.stopwords = frozenset(stopwords or DEFAULT_STOPWORDS)

    def tokenize(self, text: str) -> list[str]:
        technical_tokens: list[str] = []

        def capture(match: re.Match[str]) -> str:
            # 技术标识符统一小写后保留为完整 token。
            technical_tokens.append(match.group(0).lower())
            # 原位置替换为空格，避免后续 jieba 再重复切一遍。
            return " "

        # 先抽技术 token，再对剩余中文文本分词。
        chinese_text = TECHNICAL_TOKEN_RE.sub(capture, text)
        chinese_tokens = [
            token.strip().lower()
            for token in jieba.lcut_for_search(chinese_text)
            if self._is_search_token(token)
        ]
        return technical_tokens + chinese_tokens

    def _is_search_token(self, token: str) -> bool:
        normalized = token.strip().lower()
        # 过滤空 token、停用词和纯标点。
        return (
            bool(normalized)
            and normalized not in self.stopwords
            and SEARCHABLE_CHARACTER_RE.search(normalized) is not None
        )
