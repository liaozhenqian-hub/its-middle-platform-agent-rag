"""Safely curate quality evaluation cases into purpose-specific suites."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import json
from pathlib import Path
import sqlite3
from typing import Any

from knowledge.quality.critical_cases import (
    OFFICIAL_CRITICAL_CASE_IDS,
    RESERVE_CRITICAL_CASE_IDS,
    definition_for,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATABASE = PROJECT_ROOT / "storage" / "agent_quality.db"
DEFAULT_SEED = (
    PROJECT_ROOT / "storage" / "evaluations" / "real-business-regression-cases-60.json"
)
DEFAULT_MANIFEST_DIR = PROJECT_ROOT / "storage" / "evaluations"
DEFAULT_BACKUP_DIR = PROJECT_ROOT / "storage" / "backups"


ROUTING_SMOKE_CASE_IDS: frozenset[str] = frozenset(
    {
        "public-intent-100:guard-cross-approval-workflow",
        "public-intent-100:guard-cross-workflow-metric",
        "public-intent-100:guard-ood-tech",
        "public-intent-100:approval-add-sign-colloquial",
        "public-intent-100:approval-dynamic-assignee-formal",
        "public-intent-100:approval-instance-state-colloquial",
        "public-intent-100:approval-parallel-reject-formal",
        "public-intent-100:approval-revoke-permission-colloquial",
        "public-intent-100:approval-transfer-delegate-colloquial",
        "public-intent-100:approval-version-migration-formal",
        "public-intent-100:metric-2c-packages-colloquial",
        "public-intent-100:metric-app-confirm-formal",
        "public-intent-100:metric-dimension-filter-colloquial",
        "public-intent-100:metric-exact-fuzzy-formal",
        "public-intent-100:metric-query-permission-colloquial",
        "public-intent-100:metric-sales-candidates-formal",
        "public-intent-100:metric-types-colloquial",
        "public-intent-100:workflow-callback-timeout-colloquial",
        "public-intent-100:workflow-connector-variable-colloquial",
        "public-intent-100:workflow-exception-branch-formal",
        "public-intent-100:workflow-loop-formal",
        "public-intent-100:workflow-retry-colloquial",
        "public-intent-100:workflow-variable-scope-formal",
        "public-intent-100:workflow-version-colloquial",
    }
)


@dataclass(frozen=True)
class CurationAction:
    case_id: str
    action: str
    source_suite: str | None
    target_suite: str | None
    enabled: bool
    reason: str


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    return (
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        is not None
    )


def _has_results(connection: sqlite3.Connection, case_id: str) -> bool:
    if not _table_exists(connection, "eval_results"):
        return False
    return (
        connection.execute(
            "SELECT 1 FROM eval_results WHERE case_id=? LIMIT 1", (case_id,)
        ).fetchone()
        is not None
    )


def plan_curation(connection: sqlite3.Connection) -> list[CurationAction]:
    connection.row_factory = sqlite3.Row
    rows = connection.execute(
        "SELECT id,suite,enabled,approval_state FROM eval_cases ORDER BY id"
    ).fetchall()
    known_ids = {str(row["id"]) for row in rows}
    missing_routes = sorted(ROUTING_SMOKE_CASE_IDS - known_ids)
    if missing_routes:
        raise RuntimeError(
            "routing smoke cases not found: " + ", ".join(missing_routes)
        )

    actions: list[CurationAction] = []
    official = set(OFFICIAL_CRITICAL_CASE_IDS)
    reserve = set(RESERVE_CRITICAL_CASE_IDS)
    for case_id in sorted(official - known_ids):
        actions.append(
            CurationAction(
                case_id, "restore", None, "critical-v2", True,
                "missing official Critical case",
            )
        )

    for row in rows:
        case_id = str(row["id"])
        suite = str(row["suite"])
        approval_state = str(row["approval_state"])
        if case_id in official:
            actions.append(
                CurationAction(
                    case_id, "update", suite, "critical-v2", True,
                    "official Critical accuracy case",
                )
            )
        elif case_id in reserve:
            actions.append(
                CurationAction(
                    case_id, "disable", suite, "critical-reserve", False,
                    "reserve case lacks required facts",
                )
            )
        elif case_id.startswith("public-intent-100:") and suite in {
            "routing-breadth", "routing-smoke", "routing-archive"
        }:
            selected = case_id in ROUTING_SMOKE_CASE_IDS
            actions.append(
                CurationAction(
                    case_id,
                    "update" if selected else "archive",
                    suite,
                    "routing-smoke" if selected else "routing-archive",
                    selected,
                    "representative routing expression" if selected else "redundant routing breadth case",
                )
            )
        elif suite in {"real-multi-turn", "conversation-regression"}:
            actions.append(
                CurationAction(
                    case_id, "update", suite, "conversation-regression", True,
                    "multi-turn behavior regression",
                )
            )
        elif suite == "real-high-risk":
            actions.append(
                CurationAction(
                    case_id, "disable", suite, suite, False,
                    "case lacks reviewed required facts",
                )
            )
        elif suite == "safety-evidence" and approval_state == "rejected":
            has_results = _has_results(connection, case_id)
            actions.append(
                CurationAction(
                    case_id,
                    "disable" if has_results else "delete",
                    suite,
                    suite,
                    False,
                    "preserve referenced history" if has_results else "rejected case without results",
                )
            )
    return actions


def _load_seed(seed_path: Path, case_id: str) -> dict[str, Any]:
    payload = json.loads(seed_path.read_text(encoding="utf-8"))
    for item in payload:
        if str(item.get("id")) == case_id:
            return dict(item)
    raise RuntimeError(f"official case seed not found: {case_id}")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _restore_case(
    connection: sqlite3.Connection, *, case_id: str, seed_path: Path, now: str
) -> None:
    item = _load_seed(seed_path, case_id)
    connection.execute(
        """INSERT INTO eval_cases(
               id,source_turn_id,name,question,knowledge_space_id,domain_id,
               required_tools_json,required_citation_types_json,
               required_facts_json,forbidden_facts_json,tags_json,enabled,
               created_at,updated_at,expected_behavior,max_latency_ms,
               max_tool_calls,max_citations,turns_json,task_type,suite,priority,
               approval_state,version
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            case_id, None, item["name"], item["question"],
            item.get("knowledge_space_id") or "middle-platform", item.get("domain_id"),
            _json(item.get("required_tools", [])),
            _json(item.get("required_citation_types", [])),
            _json(item.get("required_facts", [])),
            _json(item.get("forbidden_facts", [])), _json(item.get("tags", [])),
            int(bool(item.get("enabled", True))), now, now,
            item.get("expected_behavior") or "answer",
            float(item.get("max_latency_ms") or 60_000),
            int(item.get("max_tool_calls") or 6),
            int(item.get("max_citations") or 10), _json(item.get("turns", [])),
            item.get("task_type") or "unknown", item.get("suite") or "routing-breadth",
            item.get("priority") or "normal", item.get("approval_state") or "candidate", 1,
        ),
    )


def _enrich_official_cases(connection: sqlite3.Connection, *, now: str) -> None:
    for case_id in OFFICIAL_CRITICAL_CASE_IDS:
        definition = definition_for(case_id)
        row = connection.execute(
            """SELECT tags_json,required_tools_json,required_citation_types_json,
                      required_facts_json,forbidden_facts_json,task_type,
                      max_tool_calls,expected_behavior,suite,priority,
                      approval_state,enabled
               FROM eval_cases WHERE id=?""",
            (case_id,),
        ).fetchone()
        if row is None:
            raise RuntimeError(f"official case was not restored: {case_id}")
        current_tags = set(json.loads(row[0] or "[]"))
        tags = current_tags | {"official-critical-v2", "critical"}
        desired = (
            _json(sorted(tags)),
            _json(definition["required_tools"]),
            _json(definition["required_citation_types"]),
            _json(definition["required_facts"]),
            _json(definition["forbidden_facts"]),
            definition["task_type"],
            definition["max_tool_calls"],
            definition["expected_behavior"],
            "critical-v2",
            "critical",
            "approved",
            1,
        )
        current = (
            _json(sorted(current_tags)),
            str(row[1]), str(row[2]), str(row[3]), str(row[4]), str(row[5]),
            int(row[6]), str(row[7]), str(row[8]), str(row[9]), str(row[10]),
            int(row[11]),
        )
        if current == desired:
            continue
        connection.execute(
            """UPDATE eval_cases SET required_tools_json=?,
                   required_citation_types_json=?,required_facts_json=?,
                   forbidden_facts_json=?,tags_json=?,task_type=?,max_tool_calls=?,
                   expected_behavior=?,suite='critical-v2',priority='critical',
                   approval_state='approved',enabled=1,version=version+1,updated_at=?
               WHERE id=?""",
            (
                _json(definition["required_tools"]),
                _json(definition["required_citation_types"]),
                _json(definition["required_facts"]),
                _json(definition["forbidden_facts"]), _json(sorted(tags)),
                definition["task_type"], definition["max_tool_calls"],
                definition["expected_behavior"], now, case_id,
            ),
        )


def _database_counts(connection: sqlite3.Connection) -> dict[str, Any]:
    total, enabled = connection.execute(
        "SELECT COUNT(*),SUM(CASE WHEN enabled=1 THEN 1 ELSE 0 END) FROM eval_cases"
    ).fetchone()
    suites = {
        str(suite): {"total": int(count), "enabled": int(active)}
        for suite, count, active in connection.execute(
            """SELECT suite,COUNT(*),SUM(CASE WHEN enabled=1 THEN 1 ELSE 0 END)
               FROM eval_cases GROUP BY suite ORDER BY suite"""
        ).fetchall()
    }
    return {"total": int(total), "enabled": int(enabled or 0), "suites": suites}


def curate_eval_cases(
    database_path: str | Path,
    *,
    output_dir: str | Path = DEFAULT_MANIFEST_DIR,
    backup_dir: str | Path | None = None,
    seed_path: str | Path = DEFAULT_SEED,
    apply: bool,
) -> dict[str, Any]:
    database = Path(database_path)
    manifests = Path(output_dir)
    backups = Path(backup_dir) if backup_dir is not None else manifests
    seed = Path(seed_path)
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    try:
        actions = plan_curation(connection)
        action_counts: dict[str, int] = {}
        for item in actions:
            action_counts[item.action] = action_counts.get(item.action, 0) + 1
        result: dict[str, Any] = {
            "database": str(database),
            "applied": apply,
            "action_counts": action_counts,
            "actions": [asdict(item) for item in actions],
            "counts": _database_counts(connection),
            "backup_path": None,
            "manifest_path": None,
        }
        if not apply:
            return result

        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        manifests.mkdir(parents=True, exist_ok=True)
        backups.mkdir(parents=True, exist_ok=True)
        backup_path = backups / f"agent_quality-{timestamp}.db"
        manifest_path = manifests / f"eval-case-curation-{timestamp}.json"
        backup_connection = sqlite3.connect(backup_path)
        try:
            connection.backup(backup_connection)
        finally:
            backup_connection.close()
        result["backup_path"] = str(backup_path)
        result["manifest_path"] = str(manifest_path)
        manifest_path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

        now = datetime.now(UTC).isoformat()
        connection.execute("BEGIN IMMEDIATE")
        try:
            for item in actions:
                if item.action == "restore":
                    _restore_case(connection, case_id=item.case_id, seed_path=seed, now=now)
            _enrich_official_cases(connection, now=now)
            for item in actions:
                if item.case_id in OFFICIAL_CRITICAL_CASE_IDS or item.action == "restore":
                    continue
                if item.action == "delete":
                    connection.execute("DELETE FROM eval_cases WHERE id=?", (item.case_id,))
                else:
                    connection.execute(
                        """UPDATE eval_cases SET suite=?,enabled=?,version=version+1,
                               updated_at=? WHERE id=? AND (suite<>? OR enabled<>?)""",
                        (
                            item.target_suite, int(item.enabled), now, item.case_id,
                            item.target_suite, int(item.enabled),
                        ),
                    )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        result["counts"] = _database_counts(connection)
        manifest_path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        return result
    finally:
        connection.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--seed", type=Path, default=DEFAULT_SEED)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_MANIFEST_DIR)
    parser.add_argument("--backup-dir", type=Path, default=DEFAULT_BACKUP_DIR)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    result = curate_eval_cases(
        args.database,
        output_dir=args.output_dir,
        backup_dir=args.backup_dir,
        seed_path=args.seed,
        apply=args.apply,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
