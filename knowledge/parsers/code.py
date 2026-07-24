from __future__ import annotations

import fnmatch
import hashlib
import json
from pathlib import PurePosixPath
from typing import Any, Iterable

from tree_sitter import Language, Node, Parser
import tree_sitter_java
import tree_sitter_javascript
import tree_sitter_typescript

from knowledge.schemas.documents import KnowledgeChunk


_LANGUAGES = {
    ".java": Language(tree_sitter_java.language()),
    ".js": Language(tree_sitter_javascript.language()),
    ".jsx": Language(tree_sitter_javascript.language()),
    ".ts": Language(tree_sitter_typescript.language_typescript()),
    ".tsx": Language(tree_sitter_typescript.language_tsx()),
}

_SYMBOL_TYPES = {
    "abstract_class_declaration": "class",
    "class_declaration": "class",
    "interface_declaration": "interface",
    "enum_declaration": "enum",
    "annotation_type_declaration": "annotation",
    "method_declaration": "method",
    "constructor_declaration": "constructor",
    "function_declaration": "function",
    "generator_function_declaration": "function",
    "type_alias_declaration": "type",
    "method_definition": "method",
    "method_signature": "method",
    "abstract_method_signature": "method",
}

_CONTAINER_TYPES = {
    "abstract_class_declaration",
    "class_declaration",
    "interface_declaration",
    "enum_declaration",
    "annotation_type_declaration",
}

_CALLABLE_TYPES = {"method", "constructor", "function"}
_BLOCK_TYPES = {
    "block",
    "constructor_body",
    "statement_block",
    "switch_block",
}
_FUNCTION_VALUE_TYPES = {
    "arrow_function",
    "function_expression",
    "generator_function",
}
_EXTENDS_CLAUSES = {
    "superclass",
    "extends_clause",
    "extends_interfaces",
    "extends_type_clause",
}
_IMPLEMENTS_CLAUSES = {"super_interfaces", "implements_clause"}
_CALL_TYPES = {
    "call_expression",
    "method_invocation",
    "explicit_constructor_invocation",
    "new_expression",
    "object_creation_expression",
}
_CALLABLE_MODIFIERS = {
    "accessibility_modifier",
    "abstract",
    "async",
    "declare",
    "get",
    "override",
    "private",
    "protected",
    "public",
    "readonly",
    "set",
    "static",
}


class DirectoryDomainClassifier:
    """Matches catalog rules in ascending priority order; lower numbers win."""

    def __init__(self, rules: list[tuple[str, str, int]], fallback: str = "shared"):
        self.rules = sorted(rules, key=lambda item: item[2])
        self.fallback = fallback

    def classify(self, relative_path: str) -> str:
        normalized = relative_path.replace("\\", "/").lstrip("/")
        for pattern, domain_id, _priority in self.rules:
            if fnmatch.fnmatch(normalized, pattern) or PurePosixPath(normalized).match(pattern):
                return domain_id
        return self.fallback


class CodeParser:
    def __init__(
        self,
        max_file_bytes: int = 1024 * 1024,
        max_chunk_chars: int = 12_000,
    ):
        if max_file_bytes <= 0 or max_chunk_chars <= 0:
            raise ValueError("code parser size limits must be positive")
        self.max_file_bytes = max_file_bytes
        self.max_chunk_chars = max_chunk_chars

    def parse(
        self,
        relative_path: str,
        text: str,
        source_id: str,
        branch: str,
        commit_sha: str,
        domain_id: str,
        language_hint: str | None = None,
        identity_scope: str = "file",
    ) -> list[KnowledgeChunk]:
        suffix = self._normalize_language_hint(language_hint, relative_path)
        language = _LANGUAGES.get(suffix)
        encoded = text.encode("utf-8")
        if language is None or len(encoded) > self.max_file_bytes:
            return []

        tree = Parser(language).parse(encoded)
        package_name, imports = self._file_context(tree.root_node, encoded, suffix)
        exports = self._file_exports(tree.root_node, encoded)
        chunks: list[KnowledgeChunk] = []
        emitted_ids: set[str] = set()

        def walk(
            node: Node,
            container: str = "",
            scope_identity: str = identity_scope,
            structural_path: tuple[int, ...] = (),
        ) -> None:
            symbol_type = _SYMBOL_TYPES.get(node.type)
            next_container = container
            next_scope = scope_identity
            if node.type == "field_declaration":
                field_type = self._text(node.child_by_field_name("type"), encoded)
                for declarator in self._children_of_type(node, "variable_declarator"):
                    name = self._text(declarator.child_by_field_name("name"), encoded)
                    if name:
                        emit(
                            node,
                            "field",
                            name,
                            container,
                            scope_identity,
                            structural_path,
                            signature=field_type,
                        )
            elif self._is_function_variable(node):
                name = self._text(node.child_by_field_name("name"), encoded)
                if name:
                    qualified_name, symbol_scope = emit(
                        node,
                        "function",
                        name,
                        container,
                        scope_identity,
                        structural_path,
                    )
                    next_container = qualified_name
                    next_scope = symbol_scope
            elif symbol_type is not None:
                name = self._text(node.child_by_field_name("name"), encoded)
                if name:
                    qualified_name, symbol_scope = emit(
                        node,
                        symbol_type,
                        name,
                        container,
                        scope_identity,
                        structural_path,
                    )
                    if node.type in _CONTAINER_TYPES or symbol_type in _CALLABLE_TYPES:
                        next_container = qualified_name
                        next_scope = symbol_scope
            for child_index, child in enumerate(node.named_children):
                child_path = (*structural_path, child_index)
                child_scope = next_scope
                if child.type in _BLOCK_TYPES:
                    path_text = ".".join(str(index) for index in child_path)
                    child_scope = f"{next_scope}/block:{path_text}"
                walk(child, next_container, child_scope, child_path)

        def emit(
            node: Node,
            symbol_type: str,
            name: str,
            container: str,
            scope_identity: str,
            structural_path: tuple[int, ...],
            signature: str | None = None,
        ) -> tuple[str, str]:
            qualified_name = f"{container}.{name}" if container else name
            normalized_signature = (
                self._normalize_whitespace(signature)
                if signature is not None
                else self._signature(node, encoded, symbol_type)
            )
            content = self._text(node, encoded).strip()
            if not content:
                return qualified_name, scope_identity
            annotations = self._annotations(node, encoded)
            extends, implements = self._heritage(node, encoded)
            calls = self._calls(node, encoded)
            chunk_id = self._stable_id(
                source_id,
                branch,
                relative_path,
                scope_identity,
                qualified_name,
                symbol_type,
                normalized_signature,
            )
            if chunk_id in emitted_ids:
                chunk_id = self._stable_id(
                    source_id,
                    branch,
                    relative_path,
                    scope_identity,
                    qualified_name,
                    symbol_type,
                    normalized_signature,
                    "structural-path:" + ".".join(map(str, structural_path)),
                )
            emitted_ids.add(chunk_id)
            keywords = " ".join(
                part
                for part in (
                    relative_path,
                    package_name,
                    qualified_name,
                    symbol_type,
                    normalized_signature,
                    *annotations,
                    *extends,
                    *implements,
                    *calls,
                    *imports,
                    *exports,
                )
                if part
            )
            content_segments = self._split_content(content, self.max_chunk_chars)
            segment_count = len(content_segments)
            for segment_index, (segment, start_offset, end_offset) in enumerate(
                content_segments
            ):
                segment_id = (
                    chunk_id
                    if segment_count == 1
                    else self._stable_id(chunk_id, f"segment:{segment_index}")
                )
                heading = (
                    qualified_name
                    if segment_count == 1
                    else f"{qualified_name} [part {segment_index + 1}/{segment_count}]"
                )
                chunks.append(
                    KnowledgeChunk(
                        chunk_id=segment_id,
                        heading=heading,
                        content=segment,
                        metadata={
                        "chunk_id": segment_id,
                        "source_id": source_id,
                        "source_type": "code",
                        "domain_id": domain_id,
                        "branch": branch,
                        "commit_sha": commit_sha,
                        "relative_path": relative_path,
                        "language": self._language_name(suffix),
                        "symbol_type": symbol_type,
                        "symbol_name": qualified_name,
                        "package_name": package_name,
                        "imports": "\n".join(imports),
                        "imports_json": self._json(imports),
                        "exports": self._json(exports),
                        "annotations": self._json(annotations),
                        "extends": self._json(extends),
                        "implements": self._json(implements),
                        "calls": self._json(calls),
                        "signature": normalized_signature,
                        "scope_identity": scope_identity,
                        "start_line": node.start_point.row + 1 + start_offset,
                        "end_line": node.start_point.row + 1 + end_offset,
                        "heading": heading,
                        "bm25_keywords": keywords,
                        "segment_index": segment_index,
                        "segment_count": segment_count,
                        "original_char_count": len(content),
                        },
                    )
                )
            symbol_scope = (
                f"{scope_identity}/{symbol_type}:{qualified_name}{normalized_signature}"
            )
            return qualified_name, symbol_scope

        walk(tree.root_node)
        return chunks

    @staticmethod
    def _split_content(content: str, max_chars: int) -> list[tuple[str, int, int]]:
        segments: list[tuple[str, int, int]] = []
        cursor = 0
        line_offset = 0
        while cursor < len(content):
            end = min(cursor + max_chars, len(content))
            if end < len(content):
                newline = content.rfind("\n", cursor, end)
                if newline >= cursor:
                    end = newline + 1
            segment = content[cursor:end]
            newline_count = segment.count("\n")
            end_offset = line_offset + newline_count
            if segment.endswith("\n") and newline_count:
                end_offset -= 1
            segments.append((segment, line_offset, end_offset))
            line_offset += newline_count
            cursor = end
        return segments

    @classmethod
    def _file_exports(cls, root: Node, source: bytes) -> list[str]:
        exports: list[str] = []

        def collect(node: Node) -> None:
            if node.type == "export_specifier":
                name = cls._text(
                    node.child_by_field_name("alias") or node.child_by_field_name("name"),
                    source,
                )
                cls._append_unique(exports, name)
                return
            symbol_type = _SYMBOL_TYPES.get(node.type)
            if symbol_type is not None:
                cls._append_unique(exports, cls._text(node.child_by_field_name("name"), source))
                return
            if cls._is_function_variable(node):
                cls._append_unique(exports, cls._text(node.child_by_field_name("name"), source))
                return
            for child in node.named_children:
                collect(child)

        for node in root.named_children:
            if node.type == "export_statement":
                collect(node)
        return exports

    @classmethod
    def _file_context(
        cls,
        root: Node,
        source: bytes,
        suffix: str,
    ) -> tuple[str, list[str]]:
        package_name = ""
        imports: list[str] = []
        for node in root.children:
            if suffix == ".java" and node.type == "package_declaration":
                package_name = cls._text(node, source).replace("package", "", 1).strip(" ;")
            if node.type in {"import_declaration", "import_statement"}:
                imports.append(cls._text(node, source).strip().rstrip(";"))
        return package_name, imports

    @classmethod
    def _signature(cls, node: Node, source: bytes, symbol_type: str) -> str:
        if symbol_type not in _CALLABLE_TYPES:
            return ""
        declaration = node
        if cls._is_function_variable(node):
            value = node.child_by_field_name("value")
            if value is not None:
                declaration = value
        parameters = declaration.child_by_field_name("parameters")
        if parameters is None:
            parameters = declaration.child_by_field_name("parameter")
        type_parameters = declaration.child_by_field_name("type_parameters")
        return_type = declaration.child_by_field_name("return_type")
        modifiers = [
            cls._normalize_whitespace(cls._text(child, source))
            for child in declaration.children
            if child.type in _CALLABLE_MODIFIERS
        ]
        parts = [
            *modifiers,
            cls._normalize_whitespace(cls._text(type_parameters, source)),
            cls._normalize_whitespace(cls._text(parameters, source)),
            cls._normalize_type_annotation(cls._text(return_type, source)),
        ]
        return " ".join(part for part in parts if part)

    @classmethod
    def _annotations(cls, node: Node, source: bytes) -> list[str]:
        result: list[str] = []
        modifiers = next((child for child in node.named_children if child.type == "modifiers"), None)
        if modifiers is None:
            return result
        for candidate in cls._descendants(modifiers):
            if candidate.type in {"annotation", "marker_annotation", "decorator"}:
                name = cls._text(candidate.child_by_field_name("name"), source)
                if not name:
                    name = cls._text(candidate, source).lstrip("@").split("(", 1)[0]
                cls._append_unique(result, name)
        return result

    @classmethod
    def _heritage(cls, node: Node, source: bytes) -> tuple[list[str], list[str]]:
        extends: list[str] = []
        implements: list[str] = []
        candidates: list[Node] = []
        for child in node.named_children:
            if child.type == "class_heritage":
                candidates.extend(child.named_children)
            else:
                candidates.append(child)
        for candidate in candidates:
            if candidate.type in _EXTENDS_CLAUSES:
                for value in cls._clause_types(candidate, source):
                    cls._append_unique(extends, value)
            elif candidate.type in _IMPLEMENTS_CLAUSES:
                for value in cls._clause_types(candidate, source):
                    cls._append_unique(implements, value)
        return extends, implements

    @classmethod
    def _clause_types(cls, clause: Node, source: bytes) -> list[str]:
        children = list(clause.named_children)
        if len(children) == 1 and children[0].type in {"type_list", "extends_type_clause"}:
            children = list(children[0].named_children)
        values = [cls._normalize_whitespace(cls._text(child, source)) for child in children]
        if values:
            return values
        text = cls._text(clause, source).strip()
        for keyword in ("extends", "implements"):
            if text.startswith(keyword):
                text = text[len(keyword) :].strip()
        return [value.strip() for value in text.split(",") if value.strip()]

    @classmethod
    def _calls(cls, node: Node, source: bytes) -> list[str]:
        calls: list[str] = []
        for candidate in cls._descendants(node):
            if candidate.type not in _CALL_TYPES:
                continue
            function = candidate.child_by_field_name("function")
            if function is not None:
                call_name = cls._text(function, source)
            else:
                arguments = candidate.child_by_field_name("arguments")
                call_text = cls._text(candidate, source)
                if arguments is not None:
                    call_name = call_text[: max(0, arguments.start_byte - candidate.start_byte)]
                else:
                    call_name = call_text.split("(", 1)[0]
            cls._append_unique(calls, cls._normalize_whitespace(call_name))
        return calls

    @staticmethod
    def _descendants(node: Node) -> Iterable[Node]:
        stack = [node]
        while stack:
            current = stack.pop()
            yield current
            stack.extend(reversed(current.named_children))

    @staticmethod
    def _children_of_type(node: Node, node_type: str) -> Iterable[Node]:
        return (child for child in node.named_children if child.type == node_type)

    @staticmethod
    def _is_function_variable(node: Node) -> bool:
        if node.type != "variable_declarator":
            return False
        value = node.child_by_field_name("value")
        return value is not None and value.type in _FUNCTION_VALUE_TYPES

    @staticmethod
    def _normalize_whitespace(value: str | None) -> str:
        return " ".join((value or "").split())

    @classmethod
    def _normalize_type_annotation(cls, value: str | None) -> str:
        normalized = cls._normalize_whitespace(value)
        if normalized.startswith(":"):
            return f": {normalized[1:].strip()}"
        return normalized

    @staticmethod
    def _json(values: list[str]) -> str:
        return json.dumps(values, ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def _append_unique(values: list[str], value: str) -> None:
        normalized = value.strip()
        if normalized and normalized not in values:
            values.append(normalized)

    @staticmethod
    def _text(node: Node | None, source: bytes) -> str:
        if node is None:
            return ""
        return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")

    @staticmethod
    def _stable_id(*parts: str) -> str:
        digest = hashlib.sha256("\0".join(parts).encode("utf-8")).hexdigest()[:32]
        return f"code-{digest}"

    @staticmethod
    def _language_name(suffix: str) -> str:
        return {
            ".java": "java",
            ".js": "javascript",
            ".jsx": "javascript",
            ".ts": "typescript",
            ".tsx": "typescript",
        }[suffix]

    @staticmethod
    def _normalize_language_hint(language_hint: str | None, relative_path: str) -> str:
        if not language_hint:
            return PurePosixPath(relative_path).suffix.lower()
        normalized = language_hint.strip().lower()
        return normalized if normalized.startswith(".") else f".{normalized}"
