from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class CheckpointMigrationResult:
    migrated_count: int
    thread_count: int
    pending_write_count: int
    skipped_thread_count: int = 0


async def migrate_checkpoints(
    source: Any,
    target: Any,
    *,
    active_after: datetime | None = None,
) -> CheckpointMigrationResult:
    rows = [item async for item in source.alist(None)]
    rows, skipped_thread_count = _select_active_threads(rows, active_after)
    rows.sort(
        key=lambda item: (
            str(item.config.get("configurable", {}).get("thread_id", "")),
            str(item.checkpoint.get("ts") or item.checkpoint.get("id") or ""),
        )
    )
    threads: set[str] = set()
    write_count = 0
    for item in rows:
        configurable = item.config.get("configurable", {})
        thread_id = str(configurable.get("thread_id") or "")
        if not thread_id:
            continue
        threads.add(thread_id)
        source_config = item.parent_config or {
            "configurable": {"thread_id": thread_id}
        }
        configurable = dict(source_config.get("configurable", {}))
        configurable.setdefault("thread_id", thread_id)
        configurable.setdefault("checkpoint_ns", "")
        base_config = {**source_config, "configurable": configurable}
        written_config = await target.aput(
            base_config,
            item.checkpoint,
            item.metadata,
            item.checkpoint.get("channel_versions", {}),
        )
        grouped: dict[tuple[str, str], list[tuple[str, Any]]] = defaultdict(list)
        for pending in item.pending_writes or []:
            task_id, channel, value, *rest = pending
            task_path = str(rest[0]) if rest else ""
            grouped[(str(task_id), task_path)].append((str(channel), value))
        for (task_id, task_path), writes in grouped.items():
            await target.aput_writes(
                written_config,
                writes,
                task_id,
                task_path,
            )
            write_count += len(writes)
    return CheckpointMigrationResult(
        migrated_count=len(rows),
        thread_count=len(threads),
        pending_write_count=write_count,
        skipped_thread_count=skipped_thread_count,
    )


async def preview_checkpoint_migration(
    source: Any, *, active_after: datetime | None = None
) -> CheckpointMigrationResult:
    rows = [item async for item in source.alist(None)]
    selected, skipped_thread_count = _select_active_threads(rows, active_after)
    threads = {
        str(item.config.get("configurable", {}).get("thread_id") or "")
        for item in selected
    }
    threads.discard("")
    pending = sum(len(item.pending_writes or ()) for item in selected)
    return CheckpointMigrationResult(
        migrated_count=len(selected),
        thread_count=len(threads),
        pending_write_count=pending,
        skipped_thread_count=skipped_thread_count,
    )


def _select_active_threads(
    rows: list[Any], active_after: datetime | None
) -> tuple[list[Any], int]:
    if active_after is None:
        return rows, 0
    grouped: dict[str, list[Any]] = defaultdict(list)
    for item in rows:
        thread_id = str(
            item.config.get("configurable", {}).get("thread_id") or ""
        )
        if thread_id:
            grouped[thread_id].append(item)
    active_threads: set[str] = set()
    for thread_id, items in grouped.items():
        timestamps = [_checkpoint_timestamp(item) for item in items]
        known = [value for value in timestamps if value is not None]
        if not known or max(known) >= active_after:
            active_threads.add(thread_id)
    return (
        [
            item
            for item in rows
            if str(item.config.get("configurable", {}).get("thread_id") or "")
            in active_threads
        ],
        len(grouped) - len(active_threads),
    )


def _checkpoint_timestamp(item: Any) -> datetime | None:
    value = item.checkpoint.get("ts")
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed
