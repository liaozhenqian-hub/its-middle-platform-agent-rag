from __future__ import annotations

from dataclasses import dataclass, replace
import json
from pathlib import Path
import subprocess
from typing import Any, Callable, Sequence

from knowledge.parsers.code import CodeParser
from knowledge.schemas.documents import KnowledgeChunk


class VueSfcParserError(RuntimeError):
    pass


@dataclass(frozen=True)
class VueSfcSource:
    relative_path: str
    text: str
    source_id: str
    branch: str
    commit_sha: str
    domain_id: str


class VueSfcBatchParser:
    """Parses every Vue SFC in one Node invocation, then reuses CodeParser."""

    def __init__(
        self,
        helper_path: str | Path | None = None,
        runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
        code_parser: CodeParser | None = None,
        node_executable: str = "node",
    ) -> None:
        project_root = Path(__file__).resolve().parents[2]
        self.helper_path = Path(helper_path or project_root / "web/scripts/parse-vue-sfc.mjs")
        self.runner = runner
        self.code_parser = code_parser or CodeParser()
        self.node_executable = node_executable

    def parse_many(self, sources: Sequence[VueSfcSource]) -> list[KnowledgeChunk]:
        if not sources:
            return []
        by_path = {source.relative_path: source for source in sources}
        if len(by_path) != len(sources):
            raise ValueError("Vue source paths must be unique within a batch")
        payload = {
            "files": [
                {"relative_path": source.relative_path, "content": source.text}
                for source in sources
            ]
        }
        command = [self.node_executable, str(self.helper_path)]
        completed = self.runner(
            command,
            input=json.dumps(payload, ensure_ascii=False),
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        if completed.returncode != 0:
            detail = (completed.stderr or "Vue SFC helper failed").strip()
            raise VueSfcParserError(detail)
        try:
            response = json.loads(completed.stdout)
        except (TypeError, json.JSONDecodeError) as exc:
            raise VueSfcParserError("Vue SFC helper returned invalid JSON") from exc
        diagnostics = response.get("diagnostics", [])
        if diagnostics:
            messages = [str(item.get("message", item)) for item in diagnostics]
            raise VueSfcParserError("; ".join(messages))

        chunks: list[KnowledgeChunk] = []
        for parsed_file in self._parsed_files(response):
            relative_path = parsed_file.get("relative_path")
            source = by_path.get(relative_path)
            if source is None:
                raise VueSfcParserError("Vue SFC helper returned an unknown path")
            for block in parsed_file.get("blocks", []):
                chunks.extend(self._parse_block(source, block))
        return chunks

    def _parse_block(
        self,
        source: VueSfcSource,
        block: dict[str, Any],
    ) -> list[KnowledgeChunk]:
        language = str(block.get("language", "js")).lower()
        if language not in {"js", "jsx", "ts", "tsx"}:
            raise VueSfcParserError(f"unsupported Vue script language: {language}")
        kind = str(block.get("kind", "script")).lower()
        if kind not in {"script", "script_setup"}:
            raise VueSfcParserError(f"unsupported Vue script block: {kind}")
        content = block.get("content")
        start_line = block.get("start_line")
        if not isinstance(content, str) or not isinstance(start_line, int) or start_line < 1:
            raise VueSfcParserError("Vue SFC helper returned an invalid script block")
        parsed = self.code_parser.parse(
            relative_path=source.relative_path,
            text=content,
            source_id=source.source_id,
            branch=source.branch,
            commit_sha=source.commit_sha,
            domain_id=source.domain_id,
            language_hint=language,
            identity_scope=f"vue:{kind}",
        )
        offset = start_line - 1
        adjusted = []
        for chunk in parsed:
            metadata = dict(chunk.metadata)
            metadata["start_line"] += offset
            metadata["end_line"] += offset
            metadata["vue_script_start_line"] = start_line
            metadata["vue_script_kind"] = kind
            adjusted.append(replace(chunk, metadata=metadata))
        return adjusted

    @staticmethod
    def _parsed_files(response: Any) -> list[dict[str, Any]]:
        if not isinstance(response, dict) or not isinstance(response.get("files"), list):
            raise VueSfcParserError("Vue SFC helper returned an invalid payload")
        if not all(isinstance(item, dict) for item in response["files"]):
            raise VueSfcParserError("Vue SFC helper returned an invalid file entry")
        return response["files"]
