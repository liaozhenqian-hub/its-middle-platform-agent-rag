from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote, urlsplit

import httpx


@dataclass(frozen=True)
class GitLabProject:
    id: int
    path_with_namespace: str
    name: str
    web_url: str
    default_branch: str | None


@dataclass(frozen=True)
class GitLabBranch:
    name: str
    commit_sha: str


class GitLabClient:
    def __init__(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        access_token: str,
    ) -> None:
        self._client = client
        self._api_root = self._validate_base_url(base_url) + "/api/v4"
        self._access_token = access_token

    async def search_projects(self, query: str) -> list[GitLabProject]:
        response = await self._client.get(
            f"{self._api_root}/projects",
            params={"search": query, "simple": "true", "per_page": 100},
            headers=self._headers(),
        )
        response.raise_for_status()
        return [
            GitLabProject(
                id=int(item["id"]),
                path_with_namespace=str(item["path_with_namespace"]),
                name=str(item["name"]),
                web_url=str(item["web_url"]),
                default_branch=(
                    str(item["default_branch"])
                    if item.get("default_branch") is not None
                    else None
                ),
            )
            for item in response.json()
        ]

    async def list_branches(
        self,
        project_id: int | str,
        search: str = "",
    ) -> list[GitLabBranch]:
        encoded_project_id = quote(str(project_id), safe="")
        response = await self._client.get(
            f"{self._api_root}/projects/{encoded_project_id}/repository/branches",
            params={"search": search, "per_page": 100},
            headers=self._headers(),
        )
        response.raise_for_status()
        return [
            GitLabBranch(
                name=str(item["name"]),
                commit_sha=str(item["commit"]["id"]),
            )
            for item in response.json()
        ]

    async def get_branch(
        self,
        project_id: int | str,
        branch_name: str,
    ) -> GitLabBranch:
        encoded_project_id = quote(str(project_id), safe="")
        encoded_branch_name = quote(branch_name, safe="")
        response = await self._client.get(
            f"{self._api_root}/projects/{encoded_project_id}/repository/branches/"
            f"{encoded_branch_name}",
            headers=self._headers(),
        )
        response.raise_for_status()
        item = response.json()
        return GitLabBranch(
            name=str(item["name"]),
            commit_sha=str(item["commit"]["id"]),
        )

    def _headers(self) -> dict[str, str]:
        return {"PRIVATE-TOKEN": self._access_token}

    @staticmethod
    def _validate_base_url(value: str) -> str:
        normalized = value.strip().rstrip("/")
        parsed = urlsplit(normalized)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("GitLab base URL must be a clean HTTP(S) URL")
        return normalized
