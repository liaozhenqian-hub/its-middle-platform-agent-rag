from __future__ import annotations

import re


_PATH = re.compile(
    r"(?<![A-Za-z0-9_])/?(?:[A-Za-z0-9_{}.$:-]+/){2,}[A-Za-z0-9_{}.$:-]+"
)
_QUALIFIED_SYMBOL = re.compile(
    r"\b[A-Za-z_$][A-Za-z0-9_$]*(?:\.[A-Za-z_$][A-Za-z0-9_$]*)+\b"
)
_CAMEL_IDENTIFIER = re.compile(r"\b[a-z_$][A-Za-z0-9_$]*[A-Z][A-Za-z0-9_$]*\b")


def extract_exact_identifiers(query: str) -> tuple[str, ...]:
    """Extract bounded technical identifiers directly from the original query."""

    values: list[str] = []
    for match in _PATH.finditer(query):
        path = "/" + match.group(0).lstrip("/").rstrip(".,;，。；")
        values.append(path)
        segments = path.split("/")
        if len(segments) > 3 and segments[1].casefold() == segments[2].casefold():
            values.append("/" + "/".join(segments[2:]))
    values.extend(match.group(0) for match in _QUALIFIED_SYMBOL.finditer(query))
    values.extend(match.group(0) for match in _CAMEL_IDENTIFIER.finditer(query))

    bounded = []
    for value in values:
        normalized = value.strip()
        if not normalized or len(normalized) > 160:
            continue
        if normalized.casefold() in {"authorization", "bearer", "contenttype"}:
            continue
        if normalized not in bounded:
            bounded.append(normalized)
        if len(bounded) >= 32:
            break
    return tuple(bounded)
