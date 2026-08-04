from __future__ import annotations

from datetime import datetime
import json
import re
from typing import Any

import aiosqlite
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from knowledge.persistence.database import DatabaseResources


_INSERT_OR_IGNORE = re.compile(r"^\s*INSERT\s+OR\s+IGNORE\s+INTO\s+", re.IGNORECASE)
_BOOLEAN_COLUMNS = {
    "cancel_requested",
    "current",
    "enabled",
    "passed",
    "resolved",
    "shared",
}


def translate_sqlite_statement(
    statement: str,
    parameters: tuple[Any, ...] | list[Any],
) -> tuple[str, dict[str, Any]]:
    normalized = statement.strip()
    if normalized.upper() == "BEGIN IMMEDIATE":
        return "SELECT 1", {}
    insert_or_ignore = bool(_INSERT_OR_IGNORE.match(normalized))
    if insert_or_ignore:
        normalized = _INSERT_OR_IGNORE.sub("INSERT INTO ", normalized, count=1)
    output: list[str] = []
    index = 0
    single_quote = False
    double_quote = False
    for character in normalized:
        if character == "'" and not double_quote:
            single_quote = not single_quote
        elif character == '"' and not single_quote:
            double_quote = not double_quote
        if character == "?" and not single_quote and not double_quote:
            output.append(f":p{index}")
            index += 1
        else:
            output.append(character)
    if index != len(parameters):
        raise ValueError("SQL placeholder count does not match parameters")
    translated = "".join(output)
    for column in _BOOLEAN_COLUMNS:
        translated = re.sub(
            rf"\b{column}\s*=\s*0\b",
            f"{column}=FALSE",
            translated,
            flags=re.IGNORECASE,
        )
        translated = re.sub(
            rf"\b{column}\s*=\s*1\b",
            f"{column}=TRUE",
            translated,
            flags=re.IGNORECASE,
        )
    if insert_or_ignore:
        translated = f"{translated} ON CONFLICT DO NOTHING"
    bindings = {
        f"p{position}": _normalize_parameter(value)
        for position, value in enumerate(parameters)
    }
    for column in _BOOLEAN_COLUMNS:
        for match in re.finditer(
            rf"\b{column}\s*=\s*:p(\d+)\b",
            translated,
            flags=re.IGNORECASE,
        ):
            key = f"p{match.group(1)}"
            if bindings.get(key) in {0, 1, False, True}:
                bindings[key] = bool(bindings[key])
    translated = _coerce_insert_boolean_values(translated, bindings)
    return translated, bindings


class PostgresCompatCursor:
    def __init__(self, result: Any) -> None:
        self._result = result
        self.rowcount = result.rowcount

    async def fetchone(self):
        row = self._result.mappings().fetchone()
        return _normalize_row(row)

    async def fetchall(self):
        return [_normalize_row(row) for row in self._result.mappings().fetchall()]


class PostgresCompatConnection:
    """Small aiosqlite-shaped adapter used while repositories share one contract."""

    def __init__(self, resources: DatabaseResources) -> None:
        self.resources = resources
        self.connection = None

    async def __aenter__(self) -> "PostgresCompatConnection":
        self.connection = await self.resources.engine.connect()
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        if self.connection is None:
            return
        if exc_type is not None and self.connection.in_transaction():
            await self.connection.rollback()
        elif self.connection.in_transaction():
            await self.connection.commit()
        await self.connection.close()

    @property
    def in_transaction(self) -> bool:
        return bool(self.connection and self.connection.in_transaction())

    async def execute(
        self,
        statement: str,
        parameters: tuple[Any, ...] | list[Any] = (),
    ) -> PostgresCompatCursor:
        assert self.connection is not None
        translated, bindings = translate_sqlite_statement(statement, parameters)
        try:
            result = await self.connection.execute(text(translated), bindings)
        except IntegrityError as exc:
            raise aiosqlite.IntegrityError(str(exc.orig)) from exc
        return PostgresCompatCursor(result)

    async def commit(self) -> None:
        assert self.connection is not None
        await self.connection.commit()

    async def rollback(self) -> None:
        assert self.connection is not None
        await self.connection.rollback()


def _normalize_row(row: Any | None):
    if row is None:
        return None
    return CompatRow({
        key: _normalize_value(value)
        for key, value in dict(row).items()
    })


class CompatRow(dict):
    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self.values())[key]
        return super().__getitem__(key)


def _normalize_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return value


def _normalize_parameter(value: Any) -> Any:
    if isinstance(value, str) and "T" in value:
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return value
        if parsed.tzinfo is not None:
            return parsed
    return value


def _coerce_insert_boolean_values(
    statement: str,
    bindings: dict[str, Any],
) -> str:
    match = re.search(
        r"INSERT\s+INTO\s+[^()\s]+\s*\((.*?)\)\s*VALUES\s*\((.*?)\)",
        statement,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if match is None:
        return statement
    columns = [item.strip().strip('"').casefold() for item in match.group(1).split(",")]
    values = [item.strip() for item in match.group(2).split(",")]
    if len(columns) != len(values):
        return statement
    for index, column in enumerate(columns):
        if column not in _BOOLEAN_COLUMNS:
            continue
        if values[index] in {"0", "1"}:
            values[index] = "TRUE" if values[index] == "1" else "FALSE"
        elif re.fullmatch(r":p\d+", values[index]):
            key = values[index][1:]
            if bindings.get(key) in {0, 1, False, True}:
                bindings[key] = bool(bindings[key])
    start, end = match.span(2)
    return statement[:start] + ",".join(values) + statement[end:]
