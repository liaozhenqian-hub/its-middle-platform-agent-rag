from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol
from urllib.parse import urlparse

import httpx
import yaml

from knowledge.retrieval.tokenizer import JiebaSearchTokenizer


class SwaggerUrlNotAllowedError(ValueError):
    pass


@dataclass(frozen=True)
class SwaggerSource:
    source_id: str
    url: str
    auth_type: str
    username: str = ""
    password: str = ""
    bearer_token: str = ""
    timeout_seconds: float = 15.0


@dataclass(frozen=True)
class SwaggerCacheEntry:
    specification: dict[str, Any]
    etag: str | None
    last_modified: str | None
    refreshed_at: datetime


class SwaggerCache(Protocol):
    async def get(self, source_id: str) -> SwaggerCacheEntry | None: ...

    async def put(self, source_id: str, entry: SwaggerCacheEntry) -> None: ...


class InMemorySwaggerCache:
    def __init__(self):
        self._entries: dict[str, SwaggerCacheEntry] = {}

    async def get(self, source_id: str) -> SwaggerCacheEntry | None:
        return self._entries.get(source_id)

    async def put(self, source_id: str, entry: SwaggerCacheEntry) -> None:
        self._entries[source_id] = entry


class SwaggerInspector:
    _METHODS = {"get", "post", "put", "patch", "delete", "options", "head", "trace"}

    def __init__(
        self,
        client: httpx.AsyncClient,
        cache: SwaggerCache,
        allowed_hosts: set[str],
        tokenizer: JiebaSearchTokenizer | None = None,
    ):
        self.client = client
        self.cache = cache
        self.allowed_hosts = {host.strip().lower() for host in allowed_hosts if host.strip()}
        self.tokenizer = tokenizer or JiebaSearchTokenizer()

    async def inspect(
        self,
        source: SwaggerSource,
        question: str,
        top_k: int = 5,
    ) -> dict[str, Any]:
        self._validate_url(source.url)
        cached = await self.cache.get(source.source_id)
        stale = False
        try:
            current = await self._refresh(source, cached)
        except (httpx.HTTPError, ValueError, yaml.YAMLError):
            if cached is None:
                raise
            current = cached
            stale = True

        operations = self._operations(current.specification)
        ranked = self._rank(question, operations)[:top_k]
        return {
            "source_id": source.source_id,
            "stale": stale,
            "etag": current.etag,
            "last_modified": current.last_modified,
            "refreshed_at": current.refreshed_at.astimezone(timezone.utc).isoformat(),
            "operations": ranked,
        }

    async def _refresh(
        self,
        source: SwaggerSource,
        cached: SwaggerCacheEntry | None,
    ) -> SwaggerCacheEntry:
        headers: dict[str, str] = {"Accept": "application/json, application/yaml, text/yaml"}
        if cached and cached.etag:
            headers["If-None-Match"] = cached.etag
        if cached and cached.last_modified:
            headers["If-Modified-Since"] = cached.last_modified
        if source.auth_type == "bearer" and source.bearer_token:
            headers["Authorization"] = f"Bearer {source.bearer_token}"
        elif source.auth_type == "basic":
            credentials = base64.b64encode(
                f"{source.username}:{source.password}".encode("utf-8")
            ).decode("ascii")
            headers["Authorization"] = f"Basic {credentials}"
        elif source.auth_type != "none":
            raise ValueError("unsupported Swagger authentication type")

        response = await self.client.get(
            source.url,
            headers=headers,
            timeout=source.timeout_seconds,
            follow_redirects=False,
        )
        if response.status_code == 304:
            if cached is None:
                raise ValueError("Swagger returned 304 without a cache entry")
            return cached
        response.raise_for_status()
        specification = yaml.safe_load(response.text)
        if not isinstance(specification, dict) or not (
            specification.get("openapi") or specification.get("swagger")
        ):
            raise ValueError("response is not an OpenAPI or Swagger specification")
        entry = SwaggerCacheEntry(
            specification=specification,
            etag=response.headers.get("etag"),
            last_modified=response.headers.get("last-modified"),
            refreshed_at=datetime.now(timezone.utc),
        )
        await self.cache.put(source.source_id, entry)
        return entry

    def _validate_url(self, url: str) -> None:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        if parsed.scheme not in {"http", "https"} or not host or host not in self.allowed_hosts:
            raise SwaggerUrlNotAllowedError("Swagger URL host is not allowed")
        if parsed.username or parsed.password:
            raise SwaggerUrlNotAllowedError("credentials in Swagger URL are not allowed")

    def _operations(self, specification: dict[str, Any]) -> list[dict[str, Any]]:
        results = []
        paths = specification.get("paths") or {}
        if not isinstance(paths, dict):
            return results
        for path, path_item in paths.items():
            if not isinstance(path_item, dict):
                continue
            common_parameters = path_item.get("parameters") or []
            for method, operation in path_item.items():
                if method.lower() not in self._METHODS or not isinstance(operation, dict):
                    continue
                parameters = [*common_parameters, *(operation.get("parameters") or [])]
                results.append(
                    {
                        "operation_id": str(operation.get("operationId") or ""),
                        "method": method.upper(),
                        "path": str(path),
                        "summary": str(operation.get("summary") or operation.get("description") or ""),
                        "tags": [str(tag) for tag in operation.get("tags") or []],
                        "parameters": [self._parameter(item) for item in parameters if isinstance(item, dict)],
                        "responses": self._responses(operation.get("responses")),
                    }
                )
        return results

    @staticmethod
    def _parameter(value: dict[str, Any]) -> dict[str, Any]:
        schema = value.get("schema") if isinstance(value.get("schema"), dict) else {}
        return {
            "name": str(value.get("name") or ""),
            "in": str(value.get("in") or ""),
            "required": bool(value.get("required", False)),
            "type": str(schema.get("type") or value.get("type") or ""),
            "description": str(value.get("description") or ""),
        }

    @staticmethod
    def _responses(value: Any) -> dict[str, str]:
        if not isinstance(value, dict):
            return {}
        return {
            str(code): str(details.get("description") or "")
            for code, details in value.items()
            if isinstance(details, dict)
        }

    def _rank(self, question: str, operations: list[dict[str, Any]]) -> list[dict[str, Any]]:
        query_tokens = set(self.tokenizer.tokenize(question))

        def score(operation: dict[str, Any]) -> tuple[int, str, str]:
            searchable = " ".join(
                [
                    operation["operation_id"],
                    operation["method"],
                    operation["path"],
                    operation["summary"],
                    *operation["tags"],
                    *(parameter["name"] for parameter in operation["parameters"]),
                ]
            )
            tokens = set(self.tokenizer.tokenize(searchable))
            overlap = len(query_tokens & tokens)
            lowered = searchable.lower()
            exact_bonus = sum(2 for token in query_tokens if token and token in lowered)
            return (-(overlap + exact_bonus), operation["path"], operation["method"])

        return sorted(operations, key=score)
