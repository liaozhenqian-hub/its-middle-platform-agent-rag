"""Enrich the official 30-case critical regression suite.

Usage:
    .venv-agent\\Scripts\\python.exe -m knowledge.migrations.enrich_critical_eval_cases --dry-run
    .venv-agent\\Scripts\\python.exe -m knowledge.migrations.enrich_critical_eval_cases --apply
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from knowledge.quality.critical_cases import (
    CRITICAL_CASE_DEFINITIONS,
    OFFICIAL_CRITICAL_CASE_IDS,
    RESERVE_CRITICAL_CASE_IDS,
    definition_for,
)


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def enrich(database_path: str | Path, *, apply: bool) -> dict[str, object]:
    path = Path(database_path)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    try:
        known = {
            str(row["id"]): row
            for row in connection.execute("SELECT * FROM eval_cases")
        }
        all_ids = set(OFFICIAL_CRITICAL_CASE_IDS) | set(RESERVE_CRITICAL_CASE_IDS)
        missing = sorted(all_ids - set(known))
        if missing:
            raise RuntimeError(f"evaluation cases not found: {', '.join(missing)}")
        changes: list[dict[str, object]] = []
        now = datetime.now(UTC).isoformat()
        for case_id in OFFICIAL_CRITICAL_CASE_IDS:
            definition = definition_for(case_id)
            row = known[case_id]
            tags = set(json.loads(row["tags_json"] or "[]"))
            tags.update({"official-critical-v2", "critical"})
            changes.append({"id": case_id, "suite": "critical-v2", "tags": sorted(tags)})
            if apply:
                connection.execute(
                    """UPDATE eval_cases SET required_tools_json=?,
                       required_citation_types_json=?, required_facts_json=?,
                       forbidden_facts_json=?, tags_json=?, task_type=?,
                       max_tool_calls=?, expected_behavior=?,
                       suite='critical-v2', priority='critical', enabled=1,
                       updated_at=? WHERE id=?""",
                    (
                        _json(definition["required_tools"]),
                        _json(definition["required_citation_types"]),
                        _json(definition["required_facts"]),
                        _json(definition["forbidden_facts"]),
                        _json(sorted(tags)),
                        definition["task_type"],
                        definition["max_tool_calls"],
                        definition["expected_behavior"],
                        now,
                        case_id,
                    ),
                )
        for case_id in RESERVE_CRITICAL_CASE_IDS:
            row = known[case_id]
            tags = set(json.loads(row["tags_json"] or "[]"))
            tags.update({"reserve-critical", "critical"})
            changes.append({"id": case_id, "suite": "critical-reserve", "tags": sorted(tags)})
            if apply:
                connection.execute(
                    "UPDATE eval_cases SET suite='critical-reserve', priority='high', tags_json=?, updated_at=? WHERE id=?",
                    (_json(sorted(tags)), now, case_id),
                )
        if apply:
            connection.commit()
        return {
            "database": str(path),
            "official_count": len(OFFICIAL_CRITICAL_CASE_IDS),
            "reserve_count": len(RESERVE_CRITICAL_CASE_IDS),
            "applied": apply,
            "changes": changes,
        }
    finally:
        connection.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", default="storage/agent_quality.db")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    result = enrich(args.database, apply=args.apply)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
