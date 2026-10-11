"""
Loads schema_graph.json and flattens it into lookup entries for fuzzy matching.

Each entry is: {term: str, type: "table"|"column"|"synonym", ref: str}
where *ref* is the canonical qualified name (e.g. "orders.o_orderdate").
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from nl2sql_ir.config import SCHEMA_GRAPH_PATH


def _load_raw(path: Path | None = None) -> dict[str, Any]:
    """Load the raw schema graph JSON."""
    path = path or SCHEMA_GRAPH_PATH
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


@lru_cache(maxsize=1)
def get_schema_graph(path: Path | None = None) -> dict[str, Any]:
    """Return the parsed schema graph (cached)."""
    return _load_raw(path)


def get_schema_terms(path: Path | None = None) -> list[dict[str, str]]:
    """
    Flatten the schema graph into a list of lookup entries.

    Returns a list of dicts, each with keys:
      - term: the string to match against (lowercased)
      - type: "table" | "column" | "synonym"
      - ref:  canonical reference (table name or table.column)
    """
    graph = _load_raw(path)
    entries: list[dict[str, str]] = []

    for table_name, table_def in graph["tables"].items():
        # Table-level entry
        entries.append({
            "term": table_name.lower(),
            "type": "table",
            "ref": table_name,
        })

        for col_name, col_def in table_def["columns"].items():
            qualified = f"{table_name}.{col_name}"

            # Column name entry
            entries.append({
                "term": col_name.lower(),
                "type": "column",
                "ref": qualified,
            })

            # Synonym entries
            for syn in col_def.get("synonyms", []):
                entries.append({
                    "term": syn.lower(),
                    "type": "synonym",
                    "ref": qualified,
                })

    return entries


def get_default_time_column(table: str, path: Path | None = None) -> str | None:
    """
    Return the default time column for a table, or None if the table
    has no registered default_time_column.
    """
    graph = _load_raw(path)
    table_def = graph["tables"].get(table)
    if table_def is None:
        return None
    col = table_def.get("default_time_column")
    if col:
        return f"{table}.{col}"
    return None


def get_time_tagged_columns(path: Path | None = None) -> list[dict[str, Any]]:
    """
    Return all columns tagged with "time", along with their synonyms.

    Each entry: {ref: "table.column", synonyms: [...], table: str}
    """
    graph = _load_raw(path)
    results: list[dict[str, Any]] = []
    for table_name, table_def in graph["tables"].items():
        for col_name, col_def in table_def["columns"].items():
            if "time" in col_def.get("tags", []):
                results.append({
                    "ref": f"{table_name}.{col_name}",
                    "synonyms": [s.lower() for s in col_def.get("synonyms", [])],
                    "table": table_name,
                })
    return results
