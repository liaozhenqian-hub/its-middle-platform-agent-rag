"""Source-aware code and document parsers."""

from knowledge.parsers.code import CodeParser, DirectoryDomainClassifier
from knowledge.parsers.documents import (
    DocumentParseDiagnostic,
    DocumentParseResult,
    DocumentParser,
)
from knowledge.parsers.policy import SourceFileDecision, SourceFilePolicy
from knowledge.parsers.vue import VueSfcBatchParser, VueSfcParserError, VueSfcSource

__all__ = [
    "CodeParser",
    "DirectoryDomainClassifier",
    "DocumentParser",
    "DocumentParseDiagnostic",
    "DocumentParseResult",
    "SourceFileDecision",
    "SourceFilePolicy",
    "VueSfcBatchParser",
    "VueSfcParserError",
    "VueSfcSource",
]
