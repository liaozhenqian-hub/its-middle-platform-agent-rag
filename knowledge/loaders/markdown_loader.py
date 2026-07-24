import hashlib
import re
from pathlib import Path
from typing import Any

import yaml

from knowledge.schemas.documents import KnowledgeChunk, MarkdownLoadResult


HEADING_RE = re.compile(r"^(##|###)\s+(.+?)\s*$")
CHUNK_META_RE = re.compile(r"^>\s*([A-Za-z0-9_]+):\s*(.*?)\s*$")


class MarkdownKnowledgeLoader:
    """Load RAG-optimized Markdown into metadata-rich chunks."""

    def __init__(self, max_chunk_chars: int = 1800, overlap_chars: int = 180):
        if max_chunk_chars < 100:
            raise ValueError("max_chunk_chars must be at least 100")
        if overlap_chars < 0:
            raise ValueError("overlap_chars cannot be negative")
        if overlap_chars >= max_chunk_chars:
            raise ValueError("overlap_chars must be smaller than max_chunk_chars")
        self.max_chunk_chars = max_chunk_chars
        self.overlap_chars = overlap_chars

    def load(self, source_path: str | Path) -> MarkdownLoadResult:
        path = Path(source_path)
        text = path.read_text(encoding="utf-8")
        frontmatter, body = self._split_frontmatter(text)
        raw_chunks = self._extract_heading_chunks(body, path, frontmatter)
        chunks = [part for chunk in raw_chunks for part in self._split_if_needed(chunk)]
        return MarkdownLoadResult(
            source_path=str(path),
            frontmatter=frontmatter,
            chunks=chunks,
        )

    @staticmethod
    def _split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
        if not text.startswith("---"):
            return {}, text
        match = re.match(r"^---\s*\n(.*?)\n---\s*\n?", text, flags=re.DOTALL)
        if not match:
            return {}, text
        data = yaml.safe_load(match.group(1)) or {}
        if not isinstance(data, dict):
            data = {}
        return data, text[match.end() :]

    def _extract_heading_chunks(
        self,
        body: str,
        source_path: Path,
        frontmatter: dict[str, Any],
    ) -> list[KnowledgeChunk]:
        lines = body.splitlines()
        headings: list[tuple[int, str, str]] = []
        for index, line in enumerate(lines):
            match = HEADING_RE.match(line)
            if match:
                headings.append((index, match.group(1), match.group(2).strip()))

        chunks: list[KnowledgeChunk] = []
        current_h2 = ""
        for idx, (start, level, heading) in enumerate(headings):
            if level == "##":
                current_h2 = heading
            end = headings[idx + 1][0] if idx + 1 < len(headings) else len(lines)
            section_lines = lines[start:end]
            metadata = self._extract_block_metadata(section_lines)
            chunk_id = metadata.get("chunk_id") or self._generated_chunk_id(heading, idx)
            section_path = heading if level == "##" else f"{current_h2} > {heading}"

            full_metadata: dict[str, Any] = {
                **{f"frontmatter_{key}": value for key, value in frontmatter.items()},
                **metadata,
                "chunk_id": chunk_id,
                "parent_chunk_id": chunk_id,
                "heading": heading,
                "section_level": level,
                "section_path": section_path,
                "source_path": str(source_path),
            }
            chunks.append(
                KnowledgeChunk(
                    chunk_id=chunk_id,
                    heading=heading,
                    content="\n".join(section_lines).strip(),
                    metadata=self._sanitize_metadata(full_metadata),
                )
            )
        return chunks

    @staticmethod
    def _extract_block_metadata(section_lines: list[str]) -> dict[str, str]:
        metadata: dict[str, str] = {}
        for line in section_lines[1:40]:
            match = CHUNK_META_RE.match(line)
            if match:
                metadata[match.group(1)] = match.group(2)
                continue
            if metadata and line.strip() and not line.startswith(">"):
                break
        return metadata

    @staticmethod
    def _generated_chunk_id(heading: str, index: int) -> str:
        digest = hashlib.sha1(f"{index}:{heading}".encode("utf-8")).hexdigest()[:10]
        return f"generated-{digest}"

    def _split_if_needed(self, chunk: KnowledgeChunk) -> list[KnowledgeChunk]:
        if len(chunk.content) <= self.max_chunk_chars:
            return [chunk]

        parts: list[KnowledgeChunk] = []
        start = 0
        part_number = 1
        content = chunk.content
        while start < len(content):
            end = min(start + self.max_chunk_chars, len(content))
            if end < len(content):
                newline = content.rfind("\n", start, end)
                if newline > start + 100:
                    end = newline
            part_text = content[start:end].strip()
            part_id = f"{chunk.chunk_id}#p{part_number:03d}"
            metadata = {
                **chunk.metadata,
                "chunk_id": part_id,
                "parent_chunk_id": chunk.chunk_id,
                "chunk_part": part_number,
            }
            parts.append(
                KnowledgeChunk(
                    chunk_id=part_id,
                    heading=chunk.heading,
                    content=part_text,
                    metadata=self._sanitize_metadata(metadata),
                )
            )
            if end >= len(content):
                break
            start = max(end - self.overlap_chars, 0)
            part_number += 1
        return parts

    @staticmethod
    def _sanitize_metadata(metadata: dict[str, Any]) -> dict[str, str | int | float | bool]:
        sanitized: dict[str, str | int | float | bool] = {}
        for key, value in metadata.items():
            if value is None:
                continue
            if isinstance(value, (str, int, float, bool)):
                sanitized[key] = value
            else:
                sanitized[key] = str(value)
        return sanitized

