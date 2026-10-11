"""
IR extraction pipeline.

  1. Link schema elements (fuzzy match).
  2. Resolve time phrases.
  3. Retrieve few-shot examples.
  4. Call the local Ollama model N times via instructor.
  5. Self-consistency vote to produce the final QueryIR with per-field
     confidence.
"""

from __future__ import annotations

import json
from collections import Counter
from typing import Any

import instructor
from ollama import Client as OllamaClient

from nl2sql_ir.config import (
    FEWSHOT_TOP_K,
    OLLAMA_BASE_URL,
    OLLAMA_MODEL,
    REFERENCE_DATE,
    SELF_CONSISTENCY_N,
)
from nl2sql_ir.ir.fewshot_store import InMemoryFewShotStore, build_seeded_store
from nl2sql_ir.ir.models import QueryIR
from nl2sql_ir.linking.schema_linker import link_schema
from nl2sql_ir.linking.time_resolver import resolve_time_filter


# ---------------------------------------------------------------------------
# Module-level singletons (lazy-initialised)
# ---------------------------------------------------------------------------

_fewshot_store: InMemoryFewShotStore | None = None


def _get_fewshot_store() -> InMemoryFewShotStore:
    global _fewshot_store
    if _fewshot_store is None:
        _fewshot_store = build_seeded_store()
    return _fewshot_store


def set_fewshot_store(store: InMemoryFewShotStore) -> None:
    """Allow tests or callers to inject their own store."""
    global _fewshot_store
    _fewshot_store = store


# ---------------------------------------------------------------------------
# Prompt assembly
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are a precise NL-to-IR extraction engine for a TPC-H PostgreSQL database.
Given a natural-language question, linked schema elements, and optional time
information, produce a valid QueryIR JSON object.

Rules:
- entities: list table names involved (lowercase, e.g. "orders", "customer").
- metric: the aggregate function and column if the question asks for one;
  null otherwise.  Column must be fully qualified (table.column).
- filters: scalar predicates from the question.
- time_filter: provided by the system when a time phrase is detected.
  Copy it into your output unchanged.
- group_by: fully qualified columns to group by.
- having: a filter on the aggregate; only when the question implies one.
  If you set having, group_by MUST be non-empty.
- order_by: {column, direction} if the question implies ordering; null otherwise.
- limit: integer if the question implies a top-N or limit; null otherwise.
- subquery: only for genuinely nested semantics (e.g. "more than the average").
  Do NOT use subquery for ordinary joins.
- unsupported: set to true only if the question is completely unmappable.
- confidence: leave as an empty dict — the system populates this.

Respond ONLY with the JSON object. No markdown, no explanation.
"""


def _build_user_prompt(
    query: str,
    linked: list[dict[str, Any]],
    time_info: dict[str, Any] | None,
    examples: list[dict[str, Any]],
) -> str:
    parts: list[str] = []

    # Few-shot examples
    if examples:
        parts.append("### Similar examples for reference:")
        for i, ex in enumerate(examples, 1):
            parts.append(f"Example {i}:")
            parts.append(f"  Question: {ex['question']}")
            parts.append(f"  IR: {json.dumps(ex['ir'], indent=2)}")
        parts.append("")

    # Linked schema elements
    parts.append("### Linked schema elements:")
    if linked:
        for m in linked:
            parts.append(
                f"  - \"{m['matched_text']}\" → {m['ref']} "
                f"(type={m['type']}, score={m['score']:.0f})"
            )
    else:
        parts.append("  (none detected)")
    parts.append("")

    # Time info
    if time_info:
        parts.append("### Detected time filter (use as-is):")
        parts.append(f"  {json.dumps(time_info)}")
        parts.append("")

    # The question
    parts.append(f"### Question:\n{query}")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Single extraction call (via instructor)
# ---------------------------------------------------------------------------

def _extract_once(
    query: str,
    linked: list[dict[str, Any]],
    time_info: dict[str, Any] | None,
    examples: list[dict[str, Any]],
    model: str | None = None,
    base_url: str | None = None,
) -> QueryIR:
    """Run a single instructor-wrapped Ollama call, returning a QueryIR."""
    client = instructor.from_openai(
        OllamaClient(host=base_url or OLLAMA_BASE_URL),
        mode=instructor.Mode.JSON,
    )

    user_prompt = _build_user_prompt(query, linked, time_info, examples)

    response: QueryIR = client.chat.completions.create(
        model=model or OLLAMA_MODEL,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        response_model=QueryIR,
        max_retries=3,
    )
    return response


# ---------------------------------------------------------------------------
# Self-consistency voting
# ---------------------------------------------------------------------------

_VOTED_FIELDS = [
    "entities", "metric", "filters", "time_filter",
    "group_by", "having", "order_by", "limit", "subquery", "unsupported",
]


def _field_to_hashable(value: Any) -> str:
    """Serialize a field value to a deterministic string for comparison."""
    if value is None:
        return "__NONE__"
    if isinstance(value, (list, dict, tuple)):
        return json.dumps(value, sort_keys=True, default=str)
    return str(value)


def _majority_vote(runs: list[QueryIR]) -> QueryIR:
    """
    Compare N runs field-by-field and build a majority-vote QueryIR with
    per-field confidence scores.
    """
    n = len(runs)
    confidence: dict[str, float] = {}
    majority_values: dict[str, Any] = {}

    for field in _VOTED_FIELDS:
        # Collect serialized values across runs
        serialized = [
            _field_to_hashable(
                getattr(r, field).model_dump() if hasattr(getattr(r, field, None), "model_dump") else getattr(r, field)
            )
            for r in runs
        ]
        counts = Counter(serialized)
        winner_ser, winner_count = counts.most_common(1)[0]

        # Confidence
        confidence[field] = round(winner_count / n, 2)

        # Retrieve the actual (un-serialized) value from the first run that
        # produced the winning serialization.
        for r in runs:
            raw = getattr(r, field)
            val = raw.model_dump() if hasattr(raw, "model_dump") else raw
            if _field_to_hashable(val) == winner_ser:
                majority_values[field] = raw
                break

    # Build the final IR from majority values
    final = QueryIR(
        entities=majority_values.get("entities", []),
        metric=majority_values.get("metric"),
        filters=majority_values.get("filters", []),
        time_filter=majority_values.get("time_filter"),
        group_by=majority_values.get("group_by", []),
        having=majority_values.get("having"),
        order_by=majority_values.get("order_by"),
        limit=majority_values.get("limit"),
        subquery=majority_values.get("subquery"),
        unsupported=majority_values.get("unsupported", False),
        confidence=confidence,
    )
    return final


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def extract_ir(
    query: str,
    *,
    model: str | None = None,
    base_url: str | None = None,
    n_runs: int | None = None,
    reference_date: str | None = None,
) -> QueryIR:
    """
    Full extraction pipeline: link → time-resolve → few-shot retrieve →
    N×LLM extract → self-consistency vote → validated QueryIR.

    Parameters
    ----------
    query : str
        Natural-language question about TPC-H.
    model : str, optional
        Override the Ollama model name.
    base_url : str, optional
        Override the Ollama server URL.
    n_runs : int, optional
        Override self-consistency run count.
    reference_date : str, optional
        Override the reference date for time resolution.
    """
    runs_count = n_runs or SELF_CONSISTENCY_N

    # 1. Schema linking
    linked = link_schema(query)

    # Extract linked table names for time resolver
    linked_tables: list[str] = []
    for m in linked:
        ref = m["ref"]
        if m["type"] == "table":
            linked_tables.append(ref)
        elif "." in ref:
            linked_tables.append(ref.split(".")[0])
    linked_tables = list(dict.fromkeys(linked_tables))  # dedupe, preserve order

    # 2. Time resolution
    time_info = resolve_time_filter(
        query,
        linked_tables,
        reference_date=reference_date or REFERENCE_DATE,
    )

    # 3. Few-shot example retrieval
    store = _get_fewshot_store()
    examples = store.retrieve(query, k=FEWSHOT_TOP_K)

    # 4. N×extraction with self-consistency
    runs: list[QueryIR] = []
    for _ in range(runs_count):
        ir = _extract_once(query, linked, time_info, examples, model=model, base_url=base_url)

        # Override time_filter from the deterministic resolver if detected
        if time_info is not None:
            from nl2sql_ir.ir.models import TimeFilter

            ir.time_filter = TimeFilter(**time_info)

        runs.append(ir)

    # 5. Majority-vote
    final = _majority_vote(runs)

    # Ensure the deterministic time_filter is preserved in the final output
    if time_info is not None:
        from nl2sql_ir.ir.models import TimeFilter

        final.time_filter = TimeFilter(**time_info)

    return final
