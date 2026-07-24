"""GitLab discovery and single-branch source snapshot support."""

from knowledge.source_sync.gitlab import GitLabBranch, GitLabClient, GitLabProject
from knowledge.source_sync.repository import (
    GitChangeType,
    GitFileChange,
    GitRepositoryError,
    GitRepositoryManager,
    GitSnapshot,
)
from knowledge.source_sync.worker import (
    GitLabBranchReader,
    GitSnapshotIndexer,
    GitSnapshotManager,
    GitSourceJobProcessor,
    GitSourceUnavailableError,
    JobProcessor,
    SourceSyncWorker,
)

__all__ = [
    "GitFileChange",
    "GitChangeType",
    "GitLabBranch",
    "GitLabClient",
    "GitLabProject",
    "GitRepositoryError",
    "GitRepositoryManager",
    "GitSnapshot",
    "GitLabBranchReader",
    "GitSnapshotIndexer",
    "GitSnapshotManager",
    "GitSourceJobProcessor",
    "GitSourceUnavailableError",
    "JobProcessor",
    "SourceSyncWorker",
]
