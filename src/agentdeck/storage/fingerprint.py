"""Executable schema-shape fingerprinting for SQLite databases.

Ported from the P1 donor (`storage/migrations.py`, commit 91413fe1): hashes
what the schema actually executes as (sqlite_master DDL + table_xinfo +
foreign keys + index details) rather than trusting database self-reporting.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3


def _quoted_identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _pragma_rows(
    connection: sqlite3.Connection,
    pragma: str,
    name: str,
) -> list[list[object]]:
    quoted = _quoted_identifier(name)
    return [list(row) for row in connection.execute(f"PRAGMA {pragma}({quoted})")]


def schema_fingerprint(connection: sqlite3.Connection) -> str:
    """Hash the executable schema shape rather than database self-reporting."""
    objects = [
        {
            "type": row[0],
            "name": row[1],
            "table_name": row[2],
            "sql": row[3],
        }
        for row in connection.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_master "
            "WHERE type IN ('table', 'index') "
            "AND name NOT LIKE 'sqlite_%' AND sql IS NOT NULL "
            "ORDER BY type, name"
        )
    ]
    table_names = sorted(
        item["name"] for item in objects if item["type"] == "table"
    )
    tables: list[dict[str, object]] = []
    for table_name in table_names:
        index_list = _pragma_rows(connection, "index_list", table_name)
        index_details = [
            {
                "name": row[1],
                "columns": _pragma_rows(connection, "index_xinfo", str(row[1])),
            }
            for row in index_list
        ]
        index_details.sort(key=lambda item: str(item["name"]))
        tables.append(
            {
                "name": table_name,
                "columns": _pragma_rows(connection, "table_xinfo", table_name),
                "foreign_keys": _pragma_rows(
                    connection,
                    "foreign_key_list",
                    table_name,
                ),
                "indexes": index_list,
                "index_details": index_details,
            }
        )
    canonical = json.dumps(
        {"objects": objects, "tables": tables},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(canonical).hexdigest()
