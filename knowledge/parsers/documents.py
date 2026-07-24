from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re
from pathlib import Path, PurePosixPath

from docx import Document
from docx.oxml.table import CT_Tbl
from docx.oxml.text.paragraph import CT_P
from docx.table import Table
from docx.text.paragraph import Paragraph
from pypdf import PdfReader

from knowledge.schemas.documents import KnowledgeChunk


_MARKDOWN_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$")


@dataclass(frozen=True)
class DocumentParseDiagnostic:
    code: str
    message: str
    relative_path: str
    page_number: int | None = None


@dataclass(frozen=True)
class DocumentParseResult:
    chunks: list[KnowledgeChunk]
    diagnostics: list[DocumentParseDiagnostic]


@dataclass(frozen=True)
class _DocumentSection:
    heading: str
    content: str
    page_number: int | None = None


class DocumentParser:
    def __init__(self, max_chunk_chars: int = 1800, overlap_chars: int = 180):
        if max_chunk_chars < 100:
            raise ValueError("max_chunk_chars must be at least 100")
        if overlap_chars < 0 or overlap_chars >= max_chunk_chars:
            raise ValueError("overlap_chars must be between 0 and max_chunk_chars")
        self.max_chunk_chars = max_chunk_chars
        self.overlap_chars = overlap_chars

    def parse(
        self,
        path: str | Path,
        source_id: str,
        version: str,
        domain_id: str,
        relative_path: str | None = None,
    ) -> list[KnowledgeChunk]:
        return self.parse_with_diagnostics(
            path,
            source_id,
            version,
            domain_id,
            relative_path=relative_path,
        ).chunks

    def parse_with_diagnostics(
        self,
        path: str | Path,
        source_id: str,
        version: str,
        domain_id: str,
        relative_path: str | None = None,
    ) -> DocumentParseResult:
        file_path = Path(path)
        logical_path = self._logical_path(relative_path or file_path.name)
        suffix = file_path.suffix.lower()
        diagnostics: list[DocumentParseDiagnostic] = []
        if suffix == ".md":
            sections = self._markdown_sections(file_path.read_text(encoding="utf-8"))
        elif suffix == ".txt":
            text = file_path.read_text(encoding="utf-8")
            sections = [_DocumentSection(file_path.stem, text)] if text.strip() else []
        elif suffix == ".docx":
            sections = self._docx_sections(file_path)
        elif suffix == ".pdf":
            sections, blank_pages = self._pdf_sections(file_path)
            for page_number in blank_pages:
                diagnostics.append(
                    DocumentParseDiagnostic(
                        code="ocr_required",
                        message=(
                            f"PDF page {page_number} contains no extractable text; "
                            "OCR is not supported"
                        ),
                        relative_path=logical_path,
                        page_number=page_number,
                    )
                )
        else:
            return DocumentParseResult(chunks=[], diagnostics=[])

        chunks: list[KnowledgeChunk] = []
        for section_index, section in enumerate(sections):
            for part_index, part in enumerate(self._split(section.content), start=1):
                identity = f"{section.heading}:{section_index}:{part_index}"
                chunk_id = self._stable_id(source_id, version, logical_path, identity)
                metadata = {
                    "chunk_id": chunk_id,
                    "source_id": source_id,
                    "source_type": "product_document",
                    "source_version": version,
                    "domain_id": domain_id,
                    "relative_path": logical_path,
                    "file_type": suffix.lstrip("."),
                    "heading": section.heading,
                    "section_index": section_index,
                    "chunk_part": part_index,
                    "bm25_keywords": f"{section.heading} {logical_path}",
                }
                if section.page_number is not None:
                    metadata["page_number"] = section.page_number
                chunks.append(
                    KnowledgeChunk(
                        chunk_id=chunk_id,
                        heading=section.heading,
                        content=part,
                        metadata=metadata,
                    )
                )
        return DocumentParseResult(chunks=chunks, diagnostics=diagnostics)

    @staticmethod
    def _markdown_sections(text: str) -> list[_DocumentSection]:
        sections: list[tuple[str, list[str]]] = []
        current_heading = "Document"
        current_lines: list[str] = []
        for line in text.splitlines():
            match = _MARKDOWN_HEADING.match(line)
            if match:
                if current_lines and "\n".join(current_lines).strip():
                    sections.append((current_heading, current_lines))
                current_heading = match.group(2).strip()
                current_lines = [line]
            else:
                current_lines.append(line)
        if current_lines and "\n".join(current_lines).strip():
            sections.append((current_heading, current_lines))
        return [
            _DocumentSection(heading, "\n".join(lines).strip())
            for heading, lines in sections
        ]

    @staticmethod
    def _docx_sections(path: Path) -> list[_DocumentSection]:
        document = Document(path)
        sections: list[tuple[str, list[str]]] = []
        heading = path.stem
        lines: list[str] = []
        for element in document.element.body.iterchildren():
            if isinstance(element, CT_P):
                paragraph = Paragraph(element, document)
                text = paragraph.text.strip()
                if not text:
                    continue
                if paragraph.style and paragraph.style.name.lower().startswith("heading"):
                    if lines:
                        sections.append((heading, lines))
                    heading = text
                    lines = [text]
                else:
                    lines.append(text)
            elif isinstance(element, CT_Tbl):
                table = Table(element, document)
                for row in table.rows:
                    line = " | ".join(cell.text.strip() for cell in row.cells)
                    if line.strip(" |"):
                        lines.append(line)
        if lines:
            sections.append((heading, lines))
        return [_DocumentSection(title, "\n".join(content)) for title, content in sections]

    @staticmethod
    def _pdf_sections(path: Path) -> tuple[list[_DocumentSection], list[int]]:
        reader = PdfReader(path)
        sections: list[_DocumentSection] = []
        blank_pages: list[int] = []
        for number, page in enumerate(reader.pages, start=1):
            text = (page.extract_text() or "").strip()
            if text:
                heading = DocumentParser._pdf_heading(text, number)
                sections.append(_DocumentSection(heading, text, number))
            else:
                blank_pages.append(number)
        return sections, blank_pages

    @staticmethod
    def _pdf_heading(text: str, page_number: int) -> str:
        first_line = next((line.strip() for line in text.splitlines() if line.strip()), "")
        if first_line and len(first_line) <= 120:
            return first_line
        return f"Page {page_number}"

    def _split(self, text: str) -> list[str]:
        normalized = text.strip()
        if not normalized:
            return []
        if len(normalized) <= self.max_chunk_chars:
            return [normalized]
        chunks = []
        start = 0
        while start < len(normalized):
            end = min(start + self.max_chunk_chars, len(normalized))
            if end < len(normalized):
                boundary = normalized.rfind("\n", start, end)
                if boundary > start + 100:
                    end = boundary
            chunks.append(normalized[start:end].strip())
            if end >= len(normalized):
                break
            start = end - self.overlap_chars
        return [chunk for chunk in chunks if chunk]

    @staticmethod
    def _stable_id(*parts: str) -> str:
        digest = hashlib.sha256("\0".join(parts).encode("utf-8")).hexdigest()[:32]
        return f"doc-{digest}"

    @staticmethod
    def _logical_path(value: str) -> str:
        return PurePosixPath(value.replace("\\", "/")).as_posix()
