from collections.abc import Mapping, Sequence
from typing import Any


_MISSING = object()
_FIELD_OPERATORS = {"$eq", "$ne", "$in", "$nin"}


def matches_metadata(
    metadata: Mapping[str, Any],
    where: Mapping[str, Any] | None,
) -> bool:
    """Match the supported Chroma-style metadata filter subset in memory."""
    if not where:
        return True

    logical_keys = [key for key in where if key.startswith("$")]
    if logical_keys:
        if len(where) != 1 or logical_keys[0] not in {"$and", "$or"}:
            raise ValueError(f"Unsupported metadata operator: {logical_keys[0]}")
        operator = logical_keys[0]
        clauses = where[operator]
        if not isinstance(clauses, list):
            raise ValueError(f"Metadata operator {operator} clauses must be a list")
        predicate = all if operator == "$and" else any
        return predicate(matches_metadata(metadata, clause) for clause in clauses)

    return all(
        _matches_field(metadata.get(field, _MISSING), condition)
        for field, condition in where.items()
    )


def _matches_field(actual: Any, condition: Any) -> bool:
    if not isinstance(condition, Mapping):
        return actual is not _MISSING and actual == condition
    if len(condition) != 1:
        raise ValueError("Metadata field condition must contain one operator")

    operator, expected = next(iter(condition.items()))
    if operator not in _FIELD_OPERATORS:
        raise ValueError(f"Unsupported metadata operator: {operator}")
    if operator in {"$in", "$nin"} and (
        not isinstance(expected, Sequence)
        or isinstance(expected, (str, bytes, bytearray))
    ):
        raise ValueError(f"Metadata operator {operator} requires a sequence")

    if operator == "$eq":
        return actual is not _MISSING and actual == expected
    if operator == "$ne":
        return actual is _MISSING or actual != expected
    if operator == "$in":
        return actual is not _MISSING and actual in expected
    return actual is _MISSING or actual not in expected
