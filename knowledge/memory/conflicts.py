from __future__ import annotations


CONFLICT_REASON_CODES = frozenset({
    "branch_mismatch",
    "environment_mismatch",
    "missing_evidence",
    "source_deleted",
    "contract_changed",
    "runtime_contradiction",
})


def validate_conflict_reason(reason_code: str) -> str:
    if reason_code not in CONFLICT_REASON_CODES:
        raise ValueError("unsupported memory conflict reason")
    return reason_code
