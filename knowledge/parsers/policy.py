from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath


@dataclass(frozen=True)
class SourceFileDecision:
    accepted: bool
    reason: str | None = None


class SourceFilePolicy:
    """Decides whether a source file is safe and useful to parse as code."""

    DEFAULT_SUFFIXES = frozenset({".java", ".js", ".jsx", ".ts", ".tsx", ".vue"})
    IGNORED_DIRECTORIES = frozenset({".git", "node_modules", "target", "dist", "build"})

    def __init__(
        self,
        max_file_bytes: int = 1024 * 1024,
        allowed_suffixes: set[str] | frozenset[str] | None = None,
    ) -> None:
        if max_file_bytes <= 0:
            raise ValueError("max_file_bytes must be positive")
        self.max_file_bytes = max_file_bytes
        suffixes = allowed_suffixes or self.DEFAULT_SUFFIXES
        self.allowed_suffixes = frozenset(self._normalize_suffix(value) for value in suffixes)

    def evaluate(self, relative_path: str, content: bytes) -> SourceFileDecision:
        normalized = PurePosixPath(relative_path.replace("\\", "/"))
        if any(part.casefold() in self.IGNORED_DIRECTORIES for part in normalized.parts):
            return SourceFileDecision(False, "ignored_directory")
        if normalized.suffix.lower() not in self.allowed_suffixes:
            return SourceFileDecision(False, "unsupported_extension")
        if len(content) > self.max_file_bytes:
            return SourceFileDecision(False, "file_too_large")
        if b"\0" in content:
            return SourceFileDecision(False, "binary")
        try:
            content.decode("utf-8")
        except UnicodeDecodeError:
            return SourceFileDecision(False, "binary")
        return SourceFileDecision(True)

    @staticmethod
    def _normalize_suffix(value: str) -> str:
        normalized = value.strip().lower()
        if not normalized:
            raise ValueError("allowed suffixes must not be empty")
        return normalized if normalized.startswith(".") else f".{normalized}"
