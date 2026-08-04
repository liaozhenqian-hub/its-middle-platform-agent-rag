import pytest

from knowledge.services.metadata_filter import matches_metadata


def test_matches_nested_boolean_and_collection_operators():
    metadata = {
        "domain": "审批流",
        "branch": "develop",
        "enabled": True,
    }

    assert matches_metadata(
        metadata,
        {
            "$and": [
                {"domain": {"$in": ["审批流", "工作流"]}},
                {
                    "$or": [
                        {"branch": "master"},
                        {"branch": {"$eq": "develop"}},
                    ]
                },
                {"enabled": {"$ne": False}},
            ]
        },
    ) is True


def test_matches_not_in_and_missing_field_semantics():
    metadata = {"domain": "审批流"}

    assert matches_metadata(metadata, {"branch": {"$nin": ["master"]}}) is True
    assert matches_metadata(metadata, {"branch": {"$eq": "develop"}}) is False
    assert matches_metadata(metadata, {"branch": {"$ne": "develop"}}) is True


def test_matches_multiple_plain_fields_as_conjunction():
    metadata = {"app_id": "middle-platform", "domain": "审批流"}

    assert matches_metadata(
        metadata,
        {"app_id": "middle-platform", "domain": "审批流"},
    ) is True
    assert matches_metadata(
        metadata,
        {"app_id": "middle-platform", "domain": "工作流"},
    ) is False


@pytest.mark.parametrize(
    "where",
    [
        {"domain": {"$contains": "审批"}},
        {"$not": {"domain": "审批流"}},
        {"$and": {"domain": "审批流"}},
    ],
)
def test_matcher_rejects_unsupported_or_malformed_conditions(where):
    with pytest.raises(ValueError, match="Unsupported|must be a list"):
        matches_metadata({"domain": "审批流"}, where)
