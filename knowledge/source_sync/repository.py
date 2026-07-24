from __future__ import annotations

import os
import re
import signal
import shlex
import shutil
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Iterator, Mapping
from urllib.parse import urlsplit


class GitRepositoryError(RuntimeError):
    pass


class GitChangeType(str, Enum):
    ADDED = "added"
    MODIFIED = "modified"
    DELETED = "deleted"
    RENAMED = "renamed"


@dataclass(frozen=True)
class GitFileChange:
    status: GitChangeType
    path: str
    previous_path: str | None = None


@dataclass(frozen=True)
class GitSnapshot:
    commit_sha: str
    mirror_path: Path
    worktree_path: Path
    full_reconcile: bool
    changes: tuple[GitFileChange, ...]


class GitRepositoryManager:
    _IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    _DEFAULT_COMMAND_TIMEOUT_SECONDS = 1800.0

    def __init__(
        self,
        storage_root: Path,
        access_token: str,
        allow_local_paths: bool = False,
        git_executable: str = "git",
        command_timeout_seconds: float = _DEFAULT_COMMAND_TIMEOUT_SECONDS,
    ) -> None:
        if command_timeout_seconds <= 0:
            raise ValueError("command_timeout_seconds must be positive")
        self._storage_root = Path(storage_root).expanduser().resolve()
        self._mirrors_root = self._storage_root / "git" / "mirrors"
        self._worktrees_root = self._storage_root / "git" / "worktrees"
        self._access_token = access_token
        self._allow_local_paths = allow_local_paths
        self._git_executable = git_executable
        self._command_timeout_seconds = command_timeout_seconds

    def prepare_snapshot(
        self,
        *,
        source_id: str,
        job_id: str,
        project_url: str,
        branch: str,
        previous_commit: str | None = None,
        force: bool = False,
    ) -> GitSnapshot:
        self._validate_identifier(source_id, "source ID")
        self._validate_identifier(job_id, "job ID")
        self._validate_branch(branch)
        clean_project_url = self._validate_project_url(project_url)

        mirror_path = self._mirrors_root / f"{source_id}.git"
        worktree_path = self._worktrees_root / job_id
        self._mirrors_root.mkdir(parents=True, exist_ok=True)
        self._worktrees_root.mkdir(parents=True, exist_ok=True)

        if not mirror_path.exists():
            self._run("init", "--bare", str(mirror_path))
        remotes = set(
            self._run(f"--git-dir={mirror_path}", "remote").splitlines()
        )
        if "origin" in remotes:
            self._run(
                f"--git-dir={mirror_path}",
                "remote",
                "set-url",
                "origin",
                clean_project_url,
            )
        else:
            self._run(
                f"--git-dir={mirror_path}",
                "remote",
                "add",
                "origin",
                clean_project_url,
            )

        self._reset_worktree(mirror_path, worktree_path)
        self._fetch_branch(
            mirror_path,
            branch,
            authenticated=urlsplit(clean_project_url).scheme in {"http", "https"},
        )
        self._remove_other_heads(mirror_path, branch)
        commit_sha = self._run(
            f"--git-dir={mirror_path}",
            "rev-parse",
            f"refs/heads/{branch}",
        ).strip()
        full_reconcile = previous_commit is None or force
        changes: tuple[GitFileChange, ...] = ()
        if not full_reconcile:
            if self._is_ancestor(mirror_path, previous_commit, commit_sha):
                changes = self._diff(mirror_path, previous_commit, commit_sha)
            else:
                full_reconcile = True
        self._run(
            f"--git-dir={mirror_path}",
            "-c",
            "core.longpaths=true",
            "worktree",
            "add",
            "--detach",
            str(worktree_path),
            commit_sha,
        )
        return GitSnapshot(
            commit_sha=commit_sha,
            mirror_path=mirror_path,
            worktree_path=worktree_path,
            full_reconcile=full_reconcile,
            changes=changes,
        )

    def cleanup(self, snapshot: GitSnapshot) -> None:
        if snapshot.worktree_path.exists():
            self._run(
                f"--git-dir={snapshot.mirror_path}",
                "-c",
                "core.longpaths=true",
                "worktree",
                "remove",
                "--force",
                str(snapshot.worktree_path),
            )
        self._run(
            f"--git-dir={snapshot.mirror_path}",
            "-c",
            "core.longpaths=true",
            "worktree",
            "prune",
        )

    def _reset_worktree(self, mirror_path: Path, worktree_path: Path) -> None:
        candidate = worktree_path.absolute()
        expected_root = self._worktrees_root.absolute()
        if candidate.parent != expected_root:
            raise ValueError("worktree path is outside the configured storage root")

        self._run(
            f"--git-dir={mirror_path}",
            "-c",
            "core.longpaths=true",
            "worktree",
            "prune",
        )
        listing = self._run(
            f"--git-dir={mirror_path}",
            "-c",
            "core.longpaths=true",
            "worktree",
            "list",
            "--porcelain",
            "-z",
        )
        registered_paths = {
            Path(field.removeprefix("worktree ")).absolute()
            for field in listing.split("\0")
            if field.startswith("worktree ")
        }
        if candidate in registered_paths:
            self._run(
                f"--git-dir={mirror_path}",
                "-c",
                "core.longpaths=true",
                "worktree",
                "remove",
                "--force",
                str(candidate),
            )

        if candidate.is_symlink():
            candidate.unlink()
        elif candidate.exists():
            resolved = candidate.resolve()
            if resolved.parent != expected_root.resolve():
                raise ValueError("stale worktree resolves outside the configured storage root")
            shutil.rmtree(candidate)
        self._run(
            f"--git-dir={mirror_path}",
            "-c",
            "core.longpaths=true",
            "worktree",
            "prune",
        )

    def _fetch_branch(
        self,
        mirror_path: Path,
        branch: str,
        *,
        authenticated: bool,
    ) -> None:
        arguments = (
            f"--git-dir={mirror_path}",
            "-c",
            "credential.helper=",
            "-c",
            "credential.username=oauth2",
            "fetch",
            "--prune",
            "--no-tags",
            "origin",
            f"+refs/heads/{branch}:refs/heads/{branch}",
        )
        if not authenticated:
            self._run(*arguments)
            return
        with self._askpass_environment() as environment:
            self._run(*arguments, env=environment)

    def _remove_other_heads(self, mirror_path: Path, branch: str) -> None:
        selected_ref = f"refs/heads/{branch}"
        refs = self._run(
            f"--git-dir={mirror_path}",
            "for-each-ref",
            "--format=%(refname)",
            "refs/heads",
        ).splitlines()
        for ref in refs:
            if ref and ref != selected_ref:
                self._run(f"--git-dir={mirror_path}", "update-ref", "-d", ref)

    @contextmanager
    def _askpass_environment(self) -> Iterator[dict[str, str]]:
        with tempfile.TemporaryDirectory(prefix="knowledge-git-askpass-") as directory:
            helper_directory = Path(directory)
            program = helper_directory / "askpass.py"
            program.write_text(
                "import os\n"
                "print(os.environ.get('KNOWLEDGE_GIT_TOKEN', ''))\n",
                encoding="utf-8",
            )
            if os.name == "nt":
                launcher = helper_directory / "askpass.cmd"
                launcher.write_text(
                    f'@"{sys.executable}" "{program}"\n',
                    encoding="utf-8",
                )
            else:
                launcher = helper_directory / "askpass"
                launcher.write_text(
                    "#!/bin/sh\n"
                    f"exec {shlex.quote(sys.executable)} {shlex.quote(str(program))}\n",
                    encoding="utf-8",
                )
                launcher.chmod(0o700)

            environment = os.environ.copy()
            environment.update(
                {
                    "GIT_ASKPASS": str(launcher),
                    "GIT_ASKPASS_REQUIRE": "force",
                    "GIT_TERMINAL_PROMPT": "0",
                    "KNOWLEDGE_GIT_TOKEN": self._access_token,
                }
            )
            yield environment

    def _is_ancestor(self, mirror_path: Path, older: str, newer: str) -> bool:
        result = subprocess.run(
            [
                self._git_executable,
                f"--git-dir={mirror_path}",
                "merge-base",
                "--is-ancestor",
                older,
                newer,
            ],
            check=False,
            capture_output=True,
            shell=False,
            timeout=self._command_timeout_seconds,
        )
        if result.returncode == 0:
            return True
        if result.returncode in {1, 128}:
            return False
        self._raise_git_error(result)
        return False

    def _diff(
        self,
        mirror_path: Path,
        older: str,
        newer: str,
    ) -> tuple[GitFileChange, ...]:
        result = subprocess.run(
            [
                self._git_executable,
                f"--git-dir={mirror_path}",
                "diff",
                "--name-status",
                "--find-renames",
                "-z",
                older,
                newer,
                "--",
            ],
            check=False,
            capture_output=True,
            shell=False,
            timeout=self._command_timeout_seconds,
        )
        if result.returncode != 0:
            self._raise_git_error(result)
        return self._parse_diff(result.stdout)

    @staticmethod
    def _parse_diff(output: bytes) -> tuple[GitFileChange, ...]:
        fields = output.split(b"\0")
        if fields and not fields[-1]:
            fields.pop()
        changes: list[GitFileChange] = []
        index = 0
        while index < len(fields):
            status_text = fields[index].decode("ascii", errors="replace")
            index += 1
            embedded_path: str | None = None
            if "\t" in status_text:
                status_text, embedded_path = status_text.split("\t", 1)
            code = status_text[:1]
            if embedded_path is None:
                if index >= len(fields):
                    raise GitRepositoryError("malformed Git diff output")
                first_path = fields[index].decode("utf-8", errors="surrogateescape")
                index += 1
            else:
                first_path = embedded_path
            if code == "R":
                if index >= len(fields):
                    raise GitRepositoryError("malformed Git rename output")
                new_path = fields[index].decode("utf-8", errors="surrogateescape")
                index += 1
                changes.append(
                    GitFileChange(GitChangeType.RENAMED, new_path, first_path)
                )
            elif code == "A":
                changes.append(GitFileChange(GitChangeType.ADDED, first_path))
            elif code == "D":
                changes.append(GitFileChange(GitChangeType.DELETED, first_path))
            elif code == "M":
                changes.append(GitFileChange(GitChangeType.MODIFIED, first_path))
            else:
                raise GitRepositoryError(f"unsupported Git change status: {status_text}")
        return tuple(changes)

    def _run(
        self,
        *arguments: str,
        env: Mapping[str, str] | None = None,
    ) -> str:
        command = [self._git_executable, *arguments]
        result = self._execute_git(command, env=env)
        if result.returncode != 0:
            self._raise_git_error(result)
        return result.stdout

    def _execute_git(
        self,
        command: list[str],
        *,
        env: Mapping[str, str] | None,
    ) -> subprocess.CompletedProcess[str]:
        process_options: dict[str, object] = {}
        if os.name == "nt":
            process_options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            process_options["start_new_session"] = True
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
            env=env,
            **process_options,
        )
        try:
            stdout, stderr = process.communicate(
                timeout=self._command_timeout_seconds
            )
        except subprocess.TimeoutExpired:
            self._terminate_process_tree(process)
            try:
                stdout, stderr = process.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                stdout, stderr = process.communicate()
            raise GitRepositoryError(
                f"Git command timed out after {self._command_timeout_seconds:g} seconds"
            ) from None
        return subprocess.CompletedProcess(
            command,
            process.returncode,
            stdout=stdout,
            stderr=stderr,
        )

    @staticmethod
    def _terminate_process_tree(process: subprocess.Popen[str]) -> None:
        if process.poll() is not None:
            return
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )
        else:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        if process.poll() is None:
            process.kill()

    def _raise_git_error(self, result: subprocess.CompletedProcess) -> None:
        raw_detail = result.stderr or result.stdout or b"Git command failed"
        if isinstance(raw_detail, bytes):
            detail = raw_detail.decode("utf-8", errors="replace").strip()
        else:
            detail = raw_detail.strip()
        if self._access_token:
            detail = detail.replace(self._access_token, "[REDACTED]")
        raise GitRepositoryError(detail)

    def _validate_project_url(self, project_url: str) -> str:
        value = project_url.strip()
        parsed = urlsplit(value)
        if parsed.scheme in {"http", "https"} and parsed.hostname:
            if (
                parsed.username is not None
                or parsed.password is not None
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError("project URL must not contain credentials, query, or fragment")
            return value
        if self._allow_local_paths:
            local_path = Path(value).expanduser().resolve()
            if local_path.exists():
                return str(local_path)
        raise ValueError("project URL must be a clean HTTP(S) URL")

    def _validate_branch(self, branch: str) -> None:
        if not branch or branch.startswith("-"):
            raise ValueError("invalid branch name")
        self._run("check-ref-format", "--branch", branch)

    @classmethod
    def _validate_identifier(cls, value: str, label: str) -> None:
        if not cls._IDENTIFIER.fullmatch(value):
            raise ValueError(f"invalid {label}")
