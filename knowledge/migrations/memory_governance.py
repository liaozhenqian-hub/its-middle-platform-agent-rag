from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from uuid import uuid4

import aiosqlite

from knowledge.config.settings import Settings
from knowledge.memory.repository import MemoryRepository


async def migrate_memory_governance(
    db_path: str | Path, *, apply: bool = False
) -> dict[str, int | bool]:
    path = Path(db_path)
    if not path.exists() and not apply:
        raise FileNotFoundError(path)
    if apply:
        repository = MemoryRepository(path)
        await repository.initialize()
    async with aiosqlite.connect(path) as database:
        async def count(sql: str) -> int:
            row = await (await database.execute(sql)).fetchone()
            return int(row[0])

        table_names = {
            str(row[0]) for row in await (await database.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )).fetchall()
        }
        candidate_columns = {
            str(row[1]) for row in await (await database.execute(
                "PRAGMA table_info(memory_candidates)"
            )).fetchall()
        }
        if "memory_procedural_specs" not in table_names:
            legacy_procedures = await count(
                "SELECT COUNT(*) FROM memory_candidates WHERE memory_type='procedural_memory'"
            )
        elif "legacy_format" not in candidate_columns:
            legacy_procedures = await count(
                """
                SELECT COUNT(*) FROM memory_candidates c
                LEFT JOIN memory_procedural_specs p ON p.record_id=c.id
                WHERE c.memory_type='procedural_memory' AND p.record_id IS NULL
                """
            )
        else:
            legacy_procedures = await count(
                """
                SELECT COUNT(*) FROM memory_candidates c
                LEFT JOIN memory_procedural_specs p ON p.record_id=c.id
                WHERE c.memory_type='procedural_memory' AND p.record_id IS NULL
                  AND COALESCE(c.legacy_format,'')!='legacy-v1'
                """
            )
        report: dict[str, int | bool] = {
            "apply": apply,
            "auto_confirm_eligible": await count(
                "SELECT COUNT(*) FROM memory_candidates WHERE status='candidate' AND scope_type='user' AND memory_type IN ('user_preference','user_context')"
            ),
            "explicit_review": await count(
                "SELECT COUNT(*) FROM memory_candidates WHERE status='candidate' AND scope_type='user' AND memory_type IN ('decision_memory','episodic_memory','procedural_memory')"
            ),
            "legacy_procedures": legacy_procedures,
            "confirmed_personal": await count(
                "SELECT COUNT(*) FROM memories WHERE scope_type='user' AND status='confirmed'"
            ),
            "domain_records": await count(
                "SELECT COUNT(*) FROM memories WHERE scope_type='domain'"
            ),
        }
        if apply:
            await database.execute(
                """
                UPDATE memory_candidates SET review_state='pending'
                WHERE scope_type='user' AND memory_type IN (
                    'decision_memory','episodic_memory','procedural_memory'
                )
                """
            )
            await database.execute(
                """
                UPDATE memory_candidates SET legacy_format='legacy-v1'
                WHERE memory_type='procedural_memory'
                  AND id NOT IN (SELECT record_id FROM memory_procedural_specs)
                """
            )
            await database.execute(
                """
                UPDATE memories SET legacy_format='legacy-v1'
                WHERE memory_type='procedural_memory'
                  AND id NOT IN (SELECT record_id FROM memory_procedural_specs)
                """
            )
            await database.execute(
                """
                INSERT INTO memory_audit_events(
                    id,memory_id,candidate_id,actor,action,details_json,created_at
                ) VALUES(?,NULL,NULL,'system:migration','memory_governance_migration',?,datetime('now'))
                """,
                (str(uuid4()), json.dumps(report, ensure_ascii=False)),
            )
            await database.commit()
    return report


async def _main() -> None:
    parser = argparse.ArgumentParser(description="Migrate memory governance metadata")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--db")
    args = parser.parse_args()
    settings = Settings()
    path = Path(args.db) if args.db else settings.resolved_memory_db
    print(json.dumps(
        await migrate_memory_governance(path, apply=args.apply),
        ensure_ascii=False,
        sort_keys=True,
    ))


if __name__ == "__main__":
    asyncio.run(_main())
