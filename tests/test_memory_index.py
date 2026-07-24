from knowledge.memory.index import MemoryIndex
from knowledge.memory.models import Memory


class FakeVectorRepository:
    def __init__(self):
        self.upserts = []
        self.deleted = []
        self.searches = []

    def upsert(self, chunks):
        self.upserts.extend(chunks)
        return [item.chunk_id for item in chunks]

    def delete(self, ids):
        self.deleted.extend(ids)
        return len(ids)

    def search(self, query, k=5, where=None):
        self.searches.append((query, k, where))
        return []


def _memory(**overrides):
    values = dict(
        id="memory-1",
        scope_type="user",
        owner_id="user-1",
        space_id="middle-platform",
        domain_id="approval-flow",
        memory_type="user_preference",
        subject="answer-format",
        normalized_fact="回答接口问题包含入参与出参",
        summary="用户偏好接口回答包含入参与出参",
        source_turn_id="turn-1",
        source_citations=("chunk-1",),
        confidence=0.9,
        status="confirmed",
        valid_from=None,
        valid_until=None,
        last_used_at=None,
        supersedes_id=None,
        created_at=None,
        updated_at=None,
    )
    values.update(overrides)
    return Memory(**values)


def test_memory_index_uses_separate_metadata_scope_and_deletes_by_id():
    repository = FakeVectorRepository()
    index = MemoryIndex(repository)

    index.upsert(_memory())
    assert repository.upserts[0].chunk_id == "memory-1"
    assert repository.upserts[0].metadata["source_type"] == "memory"
    assert repository.upserts[0].metadata["owner_id"] == "user-1"

    index.search(
        "接口",
        scope_type="user",
        owner_id="user-1",
        space_id="middle-platform",
        domain_id="approval-flow",
    )
    assert repository.searches == [
        (
            "接口",
            5,
            {
                "$and": [
                    {"source_type": "memory"},
                    {"scope_type": "user"},
                    {"owner_id": "user-1"},
                    {"space_id": "middle-platform"},
                    {"domain_id": "approval-flow"},
                ]
            },
        )
    ]
    index.delete("memory-1")
    assert repository.deleted == ["memory-1"]
