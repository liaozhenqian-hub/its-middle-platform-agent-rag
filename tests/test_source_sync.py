from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest


def _git(*args: str, cwd: Path | None = None) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.stdout.strip()


def _commit_file(repository: Path, relative_path: str, content: str, message: str) -> str:
    path = repository / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    _git("add", "--", relative_path, cwd=repository)
    _git("commit", "-m", message, cwd=repository)
    return _git("rev-parse", "HEAD", cwd=repository)


def _create_remote_fixture(tmp_path: Path) -> tuple[Path, Path, str, str]:
    remote = tmp_path / "remote.git"
    work = tmp_path / "seed"
    _git("init", "--bare", str(remote))
    _git("init", "--initial-branch=main", str(work))
    _git("config", "user.name", "Source Sync Test", cwd=work)
    _git("config", "user.email", "source-sync@example.test", cwd=work)
    main_commit = _commit_file(work, "README.md", "main\n", "initial main")
    _git("remote", "add", "origin", str(remote), cwd=work)
    _git("push", "-u", "origin", "main", cwd=work)

    _git("switch", "-c", "feature", cwd=work)
    feature_commit = _commit_file(work, "feature.txt", "feature\n", "feature only")
    _git("push", "-u", "origin", "feature", cwd=work)
    _git("switch", "main", cwd=work)
    return remote, work, main_commit, feature_commit


@pytest.mark.asyncio
async def test_gitlab_client_maps_projects_and_url_encoded_branches_without_exposing_token():
    from knowledge.source_sync import GitLabClient

    token = "gitlab-secret-token"
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.headers["PRIVATE-TOKEN"] == token
        assert "authorization" not in request.headers
        if request.url.path == "/api/v4/projects":
            assert parse_qs(request.url.query.decode("ascii")) == {
                "search": ["middle platform"],
                "simple": ["true"],
                "per_page": ["100"],
            }
            return httpx.Response(
                200,
                json=[
                    {
                        "id": 42,
                        "path_with_namespace": "platform/backend",
                        "name": "backend",
                        "web_url": "https://gitlab.example/platform/backend",
                        "default_branch": "main",
                        "ignored": "not public",
                    }
                ],
            )
        assert request.url.raw_path.split(b"?", 1)[0] == (
            b"/api/v4/projects/platform%2Fbackend/repository/branches"
        )
        assert parse_qs(request.url.query.decode("ascii")) == {
            "search": ["release/v1"],
            "per_page": ["100"],
        }
        return httpx.Response(
            200,
            json=[
                {
                    "name": "release/v1",
                    "commit": {"id": "abc123", "message": "must not leak"},
                    "protected": True,
                }
            ],
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = GitLabClient(http_client, "https://gitlab.example/", token)
        projects = await client.search_projects("middle platform")
        branches = await client.list_branches("platform/backend", "release/v1")

    assert projects[0].id == 42
    assert projects[0].path_with_namespace == "platform/backend"
    assert projects[0].default_branch == "main"
    assert not hasattr(projects[0], "ignored")
    assert branches[0].name == "release/v1"
    assert branches[0].commit_sha == "abc123"
    assert not hasattr(branches[0], "protected")
    assert token not in repr(projects)
    assert token not in repr(branches)
    assert len(requests) == 2


@pytest.mark.parametrize(
    "base_url",
    [
        "https://user:password@gitlab.example",
        "https://gitlab.example?token=secret",
        "https://gitlab.example/#secret",
        "file:///tmp/gitlab",
    ],
)
def test_gitlab_client_rejects_unclean_base_urls(base_url: str):
    from knowledge.source_sync import GitLabClient

    with pytest.raises(ValueError, match="GitLab base URL"):
        GitLabClient(httpx.AsyncClient(), base_url, "token")


@pytest.mark.asyncio
async def test_gitlab_client_gets_exact_branch_head_with_encoded_identifiers():
    from knowledge.source_sync import GitLabClient

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["PRIVATE-TOKEN"] == "token"
        assert request.url.raw_path == (
            b"/api/v4/projects/group%2Fproject/repository/branches/release%2Fv1"
        )
        return httpx.Response(
            200,
            json={"name": "release/v1", "commit": {"id": "deadbeef"}},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = GitLabClient(http_client, "https://gitlab.example", "token")
        branch = await client.get_branch("group/project", "release/v1")

    assert branch.name == "release/v1"
    assert branch.commit_sha == "deadbeef"


def test_repository_manager_prepares_initial_single_branch_snapshot_and_cleans_up(tmp_path: Path):
    from knowledge.source_sync import GitRepositoryManager

    remote, _, main_commit, feature_commit = _create_remote_fixture(tmp_path)
    storage_root = tmp_path / "storage"
    manager = GitRepositoryManager(
        storage_root=storage_root,
        access_token="",
        allow_local_paths=True,
    )

    snapshot = manager.prepare_snapshot(
        source_id="source-1",
        job_id="job-1",
        project_url=str(remote),
        branch="main",
    )

    assert snapshot.commit_sha == main_commit
    assert snapshot.commit_sha != feature_commit
    assert snapshot.full_reconcile is True
    assert snapshot.changes == ()
    assert snapshot.mirror_path == storage_root / "git" / "mirrors" / "source-1.git"
    assert snapshot.worktree_path == storage_root / "git" / "worktrees" / "job-1"
    assert (snapshot.worktree_path / "README.md").read_text(encoding="utf-8") == "main\n"
    assert not (snapshot.worktree_path / "feature.txt").exists()
    refs = _git(
        f"--git-dir={snapshot.mirror_path}",
        "for-each-ref",
        "--format=%(refname)",
        "refs/heads",
    ).splitlines()
    assert refs == ["refs/heads/main"]
    assert _git(
        f"--git-dir={snapshot.mirror_path}",
        "remote",
        "get-url",
        "origin",
    ) == str(remote.resolve())

    manager.cleanup(snapshot)

    assert not snapshot.worktree_path.exists()


def test_repository_manager_returns_incremental_add_delete_and_rename_changes(tmp_path: Path):
    from knowledge.source_sync import GitChangeType, GitRepositoryManager

    remote, work, _, _ = _create_remote_fixture(tmp_path)
    _commit_file(work, "obsolete.txt", "remove me\n", "add obsolete")
    baseline_commit = _commit_file(work, "keep.txt", "old\n", "add keep")
    _git("push", "origin", "main", cwd=work)

    manager = GitRepositoryManager(tmp_path / "storage", "", allow_local_paths=True)
    baseline = manager.prepare_snapshot(
        source_id="source-1",
        job_id="baseline",
        project_url=str(remote),
        branch="main",
    )
    manager.cleanup(baseline)

    (work / "docs").mkdir()
    _git("mv", "README.md", "docs/README-renamed.md", cwd=work)
    (work / "obsolete.txt").unlink()
    (work / "keep.txt").write_text("updated\n", encoding="utf-8")
    (work / "new.py").write_text("print('new')\n", encoding="utf-8")
    _git("add", "--all", cwd=work)
    _git("commit", "-m", "rename add and delete", cwd=work)
    current_commit = _git("rev-parse", "HEAD", cwd=work)
    _git("push", "origin", "main", cwd=work)

    snapshot = manager.prepare_snapshot(
        source_id="source-1",
        job_id="incremental",
        project_url=str(remote),
        branch="main",
        previous_commit=baseline_commit,
    )

    assert snapshot.commit_sha == current_commit
    assert snapshot.full_reconcile is False
    assert {(change.status, change.path, change.previous_path) for change in snapshot.changes} == {
        (GitChangeType.RENAMED, "docs/README-renamed.md", "README.md"),
        (GitChangeType.DELETED, "obsolete.txt", None),
        (GitChangeType.ADDED, "new.py", None),
        (GitChangeType.MODIFIED, "keep.txt", None),
    }
    assert (snapshot.worktree_path / "docs" / "README-renamed.md").exists()
    assert not (snapshot.worktree_path / "obsolete.txt").exists()
    manager.cleanup(snapshot)


def test_repository_manager_falls_back_to_full_reconcile_for_non_ancestor_and_force(tmp_path: Path):
    from knowledge.source_sync import GitRepositoryManager

    remote, work, baseline_commit, _ = _create_remote_fixture(tmp_path)
    manager = GitRepositoryManager(tmp_path / "storage", "", allow_local_paths=True)
    initial = manager.prepare_snapshot(
        source_id="source-1",
        job_id="initial",
        project_url=str(remote),
        branch="main",
    )
    manager.cleanup(initial)

    _git("switch", "--orphan", "rewritten-main", cwd=work)
    rewritten_commit = _commit_file(work, "rewritten.txt", "new root\n", "rewrite history")
    _git("branch", "-M", "main", cwd=work)
    _git("push", "--force", "origin", "main", cwd=work)

    rewritten = manager.prepare_snapshot(
        source_id="source-1",
        job_id="rewritten",
        project_url=str(remote),
        branch="main",
        previous_commit=baseline_commit,
    )

    assert rewritten.commit_sha == rewritten_commit
    assert rewritten.full_reconcile is True
    assert rewritten.changes == ()
    manager.cleanup(rewritten)

    descendant_commit = _commit_file(work, "next.txt", "next\n", "normal descendant")
    _git("push", "origin", "main", cwd=work)
    forced = manager.prepare_snapshot(
        source_id="source-1",
        job_id="explicit-force",
        project_url=str(remote),
        branch="main",
        previous_commit=rewritten_commit,
        force=True,
    )

    assert forced.commit_sha == descendant_commit
    assert forced.full_reconcile is True
    assert forced.changes == ()
    manager.cleanup(forced)


def test_repository_manager_injects_http_token_only_through_temporary_askpass(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    from knowledge.source_sync import GitRepositoryManager

    token = "sensitive-token-value"
    commands: list[list[str]] = []
    helper_paths: list[Path] = []
    helper_contents: list[str] = []

    def fake_execute(command, *, env=None):
        command = [str(item) for item in command]
        commands.append(command)
        if "fetch" in command:
            environment = env
            assert environment is not None
            helper = Path(environment["GIT_ASKPASS"])
            helper_paths.append(helper)
            assert helper.exists()
            helper_contents.extend(
                path.read_text(encoding="utf-8") for path in helper.parent.iterdir()
            )
            assert environment["GIT_TERMINAL_PROMPT"] == "0"
            assert environment["KNOWLEDGE_GIT_TOKEN"] == token
        stdout = "a" * 40 + "\n" if "rev-parse" in command else ""
        return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")

    manager = GitRepositoryManager(tmp_path / "storage", token)
    monkeypatch.setattr(manager, "_execute_git", fake_execute)

    snapshot = manager.prepare_snapshot(
        source_id="source-1",
        job_id="job-1",
        project_url="https://gitlab.example/platform/backend.git",
        branch="main",
    )

    assert snapshot.commit_sha == "a" * 40
    assert helper_paths
    assert all(not path.exists() for path in helper_paths)
    assert all(token not in content for content in helper_contents)
    assert all("%*" not in content and '"$@"' not in content for content in helper_contents)
    assert all(token not in " ".join(command) for command in commands)
    assert any("credential.username=oauth2" in command for command in commands)
    assert any(
        "worktree" in command
        and "add" in command
        and "core.longpaths=true" in command
        for command in commands
    )
    origin_command = next(command for command in commands if "add" in command and "origin" in command)
    assert origin_command[-1] == "https://gitlab.example/platform/backend.git"


def test_repository_manager_bounds_every_git_subprocess(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    from knowledge.source_sync import GitRepositoryManager

    observed_timeouts: list[float | None] = []

    def fake_run(command, **kwargs):
        observed_timeouts.append(kwargs.get("timeout"))
        stdout = "" if kwargs.get("text") else b""
        stderr = "" if kwargs.get("text") else b""
        return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr=stderr)

    monkeypatch.setattr("knowledge.source_sync.repository.subprocess.run", fake_run)
    manager = GitRepositoryManager(tmp_path / "storage", "")

    def fake_execute(command, *, env=None):
        observed_timeouts.append(manager._command_timeout_seconds)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(manager, "_execute_git", fake_execute)

    manager._run("--version")
    assert manager._is_ancestor(tmp_path / "mirror.git", "older", "newer") is True
    assert manager._diff(tmp_path / "mirror.git", "older", "newer") == ()

    assert len(observed_timeouts) == 3
    assert all(
        isinstance(timeout, (int, float)) and 0 < timeout <= 1800
        for timeout in observed_timeouts
    )


def test_authenticated_fetch_disables_configured_credential_helpers(
    tmp_path: Path,
):
    from knowledge.source_sync import GitRepositoryManager

    manager = GitRepositoryManager(tmp_path / "storage", "read-only-token")
    observed: dict[str, object] = {}

    def fake_run(*arguments, env=None):
        observed["arguments"] = arguments
        observed["environment"] = env
        return ""

    manager._run = fake_run
    manager._fetch_branch(tmp_path / "mirror.git", "master", authenticated=True)

    arguments = observed["arguments"]
    assert isinstance(arguments, tuple)
    assert ("-c", "credential.helper=") == arguments[1:3]
    environment = observed["environment"]
    assert isinstance(environment, dict)
    assert environment["GIT_TERMINAL_PROMPT"] == "0"


def test_git_timeout_terminates_child_process_tree_promptly(tmp_path: Path):
    from knowledge.source_sync import GitRepositoryError, GitRepositoryManager

    manager = GitRepositoryManager(
        tmp_path / "storage",
        "",
        git_executable=sys.executable,
    )
    manager._command_timeout_seconds = 0.1
    child_script = (
        "import subprocess,sys,time;"
        "subprocess.Popen([sys.executable,'-c','import time;time.sleep(3)']);"
        "time.sleep(3)"
    )

    started = time.perf_counter()
    with pytest.raises(GitRepositoryError, match="timed out"):
        manager._run("-c", child_script)

    assert time.perf_counter() - started < 2


def test_repository_manager_replaces_a_stale_credentialed_origin_before_fetch(tmp_path: Path):
    from knowledge.source_sync import GitRepositoryManager

    remote, _, main_commit, _ = _create_remote_fixture(tmp_path)
    manager = GitRepositoryManager(tmp_path / "storage", "", allow_local_paths=True)
    initial = manager.prepare_snapshot(
        source_id="source-1",
        job_id="initial",
        project_url=str(remote),
        branch="main",
    )
    manager.cleanup(initial)

    embedded_token = "embedded-origin-secret"
    _git(
        f"--git-dir={initial.mirror_path}",
        "remote",
        "set-url",
        "origin",
        f"https://oauth2:{embedded_token}@127.0.0.1:1/repository.git",
    )

    refreshed = manager.prepare_snapshot(
        source_id="source-1",
        job_id="refreshed",
        project_url=str(remote),
        branch="main",
    )

    origin = _git(
        f"--git-dir={refreshed.mirror_path}",
        "remote",
        "get-url",
        "origin",
    )
    assert refreshed.commit_sha == main_commit
    assert origin == str(remote.resolve())
    assert embedded_token not in origin
    manager.cleanup(refreshed)


def test_repository_manager_redacts_token_from_git_errors_and_removes_askpass(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    from knowledge.source_sync import GitRepositoryError, GitRepositoryManager

    token = "server-echoed-secret"
    helper_path: Path | None = None

    def fake_execute(command, *, env=None):
        nonlocal helper_path
        command = [str(item) for item in command]
        if "fetch" in command:
            assert env is not None
            helper_path = Path(env["GIT_ASKPASS"])
            return subprocess.CompletedProcess(
                command,
                1,
                stdout="",
                stderr=f"authentication failed for {token}",
            )
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    manager = GitRepositoryManager(tmp_path / "storage", token)
    monkeypatch.setattr(manager, "_execute_git", fake_execute)

    with pytest.raises(GitRepositoryError) as captured:
        manager.prepare_snapshot(
            source_id="source-1",
            job_id="job-1",
            project_url="https://gitlab.example/platform/backend.git",
            branch="main",
        )

    assert token not in str(captured.value)
    assert "[REDACTED]" in str(captured.value)
    assert helper_path is not None
    assert not helper_path.exists()


@pytest.mark.parametrize(
    "project_url",
    [
        "https://oauth2:secret@gitlab.example/group/project.git",
        "https://gitlab.example/group/project.git?private_token=secret",
        "https://gitlab.example/group/project.git#secret",
    ],
)
def test_repository_manager_rejects_project_urls_that_can_carry_credentials(
    tmp_path: Path,
    project_url: str,
):
    from knowledge.source_sync import GitRepositoryManager

    manager = GitRepositoryManager(tmp_path / "storage", "global-secret")

    with pytest.raises(ValueError, match="project URL") as captured:
        manager.prepare_snapshot(
            source_id="source-1",
            job_id="job-1",
            project_url=project_url,
            branch="main",
        )

    assert "secret" not in str(captured.value)


def test_repository_manager_reuses_job_after_registered_and_unregistered_worktree_crashes(
    tmp_path: Path,
):
    from knowledge.source_sync import GitRepositoryManager

    remote, _, main_commit, _ = _create_remote_fixture(tmp_path)
    manager = GitRepositoryManager(tmp_path / "storage", "", allow_local_paths=True)

    crashed = manager.prepare_snapshot(
        source_id="source-1",
        job_id="retry-job",
        project_url=str(remote),
        branch="main",
    )
    (crashed.worktree_path / "stale-marker.txt").write_text("stale", encoding="utf-8")

    registered_retry = manager.prepare_snapshot(
        source_id="source-1",
        job_id="retry-job",
        project_url=str(remote),
        branch="main",
    )

    assert registered_retry.commit_sha == main_commit
    assert not (registered_retry.worktree_path / "stale-marker.txt").exists()
    manager.cleanup(registered_retry)

    registered_retry.worktree_path.mkdir(parents=True)
    (registered_retry.worktree_path / "unregistered-marker.txt").write_text(
        "stale",
        encoding="utf-8",
    )
    unregistered_retry = manager.prepare_snapshot(
        source_id="source-1",
        job_id="retry-job",
        project_url=str(remote),
        branch="main",
    )

    assert unregistered_retry.commit_sha == main_commit
    assert not (unregistered_retry.worktree_path / "unregistered-marker.txt").exists()
    manager.cleanup(unregistered_retry)


def test_repository_manager_recovers_when_existing_mirror_has_no_origin(tmp_path: Path):
    from knowledge.source_sync import GitRepositoryManager

    remote, _, main_commit, _ = _create_remote_fixture(tmp_path)
    manager = GitRepositoryManager(tmp_path / "storage", "", allow_local_paths=True)
    initial = manager.prepare_snapshot(
        source_id="source-1",
        job_id="initial",
        project_url=str(remote),
        branch="main",
    )
    manager.cleanup(initial)
    _git(f"--git-dir={initial.mirror_path}", "remote", "remove", "origin")

    recovered = manager.prepare_snapshot(
        source_id="source-1",
        job_id="recovered",
        project_url=str(remote),
        branch="main",
    )

    assert recovered.commit_sha == main_commit
    assert _git(
        f"--git-dir={recovered.mirror_path}",
        "remote",
        "get-url",
        "origin",
    ) == str(remote.resolve())
    manager.cleanup(recovered)


def test_repository_manager_removes_old_local_heads_when_configured_branch_changes(
    tmp_path: Path,
):
    from knowledge.source_sync import GitRepositoryManager

    remote, _, _, feature_commit = _create_remote_fixture(tmp_path)
    manager = GitRepositoryManager(tmp_path / "storage", "", allow_local_paths=True)
    main_snapshot = manager.prepare_snapshot(
        source_id="source-1",
        job_id="main-job",
        project_url=str(remote),
        branch="main",
    )
    manager.cleanup(main_snapshot)

    feature_snapshot = manager.prepare_snapshot(
        source_id="source-1",
        job_id="feature-job",
        project_url=str(remote),
        branch="feature",
    )

    refs = _git(
        f"--git-dir={feature_snapshot.mirror_path}",
        "for-each-ref",
        "--format=%(refname)",
        "refs/heads",
    ).splitlines()
    assert feature_snapshot.commit_sha == feature_commit
    assert refs == ["refs/heads/feature"]
    assert (feature_snapshot.worktree_path / "feature.txt").exists()
    manager.cleanup(feature_snapshot)
