"""
Resolves time phrases in natural-language queries to concrete date ranges
and selects the appropriate schema column via priority logic:

  (a) Anchor word matches a time-tagged column synonym → use that column.
  (b) No anchor, one linked table with a default_time_column → use it.
  (c) Ambiguous (no anchor, multiple linked tables with time cols) → flag.

Uses dateparser for phrase resolution — time columns are NEVER resolved
by fuzzy-matching against schema terms.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any

import dateparser
from dateparser.search import search_dates

from nl2sql_ir.config import REFERENCE_DATE
from nl2sql_ir.schema.schema_loader import get_default_time_column, get_time_tagged_columns


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_QUARTER_MONTHS = {1: (1, 3), 2: (4, 6), 3: (7, 9), 4: (10, 12)}


def _parse_reference_date(ref: str) -> datetime:
    """Parse a YYYY-MM-DD string into a datetime."""
    return datetime.strptime(ref, "%Y-%m-%d")


def _infer_range(phrase: str, resolved_dt: datetime, ref_dt: datetime) -> tuple[str, str]:
    """
    Given the raw phrase and its resolved datetime, compute [start, end].

    Handles explicit years, relative quarter/year phrases, and falls back
    to a ±0-day point range for exact dates.
    """
    phrase_lower = phrase.lower().strip()

    # --- Explicit 4-digit year: "in 1996" / "1996" ---
    year_match = re.search(r"\b(1\d{3}|20\d{2})\b", phrase_lower)
    if year_match and len(phrase_lower.split()) <= 3:
        year = int(year_match.group(1))
        return (f"{year}-01-01", f"{year}-12-31")

    # --- "this year" ---
    if "this year" in phrase_lower:
        y = ref_dt.year
        return (f"{y}-01-01", f"{y}-12-31")

    # --- "last year" ---
    if "last year" in phrase_lower:
        y = ref_dt.year - 1
        return (f"{y}-01-01", f"{y}-12-31")

    # --- Quarter handling ---
    q_match = re.search(r"(last|this|q)?\s*(?:quarter|q)?\s*(\d)?", phrase_lower)
    if "quarter" in phrase_lower or re.search(r"\bq[1-4]\b", phrase_lower):
        if "last quarter" in phrase_lower:
            # quarter before the reference date's quarter
            cur_q = (ref_dt.month - 1) // 3 + 1
            target_q = cur_q - 1 if cur_q > 1 else 4
            target_y = ref_dt.year if cur_q > 1 else ref_dt.year - 1
            sm, em = _QUARTER_MONTHS[target_q]
            return (f"{target_y}-{sm:02d}-01", f"{target_y}-{em:02d}-{_last_day(target_y, em):02d}")
        elif "this quarter" in phrase_lower:
            cur_q = (ref_dt.month - 1) // 3 + 1
            sm, em = _QUARTER_MONTHS[cur_q]
            return (f"{ref_dt.year}-{sm:02d}-01", f"{ref_dt.year}-{em:02d}-{_last_day(ref_dt.year, em):02d}")
        # "Q2 1995" style
        qn_match = re.search(r"q(\d)\s*(\d{4})?", phrase_lower)
        if qn_match:
            qn = int(qn_match.group(1))
            qy = int(qn_match.group(2)) if qn_match.group(2) else ref_dt.year
            sm, em = _QUARTER_MONTHS[qn]
            return (f"{qy}-{sm:02d}-01", f"{qy}-{em:02d}-{_last_day(qy, em):02d}")

    # --- "last N months" ---
    months_match = re.search(r"last\s+(\d+)\s+months?", phrase_lower)
    if months_match:
        n = int(months_match.group(1))
        end = ref_dt
        start = _subtract_months(ref_dt, n)
        return (start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))

    # --- "last month" ---
    if "last month" in phrase_lower:
        first_of_this = ref_dt.replace(day=1)
        last_month_end = first_of_this - timedelta(days=1)
        last_month_start = last_month_end.replace(day=1)
        return (last_month_start.strftime("%Y-%m-%d"), last_month_end.strftime("%Y-%m-%d"))

    # --- "this month" ---
    if "this month" in phrase_lower:
        start = ref_dt.replace(day=1)
        end_day = _last_day(ref_dt.year, ref_dt.month)
        end = ref_dt.replace(day=end_day)
        return (start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))

    # --- Fallback: use the resolved datetime as a point ---
    ds = resolved_dt.strftime("%Y-%m-%d")
    return (ds, ds)


def _last_day(year: int, month: int) -> int:
    """Return the last day of a given month."""
    import calendar
    return calendar.monthrange(year, month)[1]


def _subtract_months(dt: datetime, months: int) -> datetime:
    """Subtract *months* from *dt*, clamping to valid day."""
    month = dt.month - months
    year = dt.year
    while month <= 0:
        month += 12
        year -= 1
    day = min(dt.day, _last_day(year, month))
    return dt.replace(year=year, month=month, day=day)


# ---------------------------------------------------------------------------
# Column-selection priority logic
# ---------------------------------------------------------------------------

def _find_anchor_column(query: str, time_columns: list[dict[str, Any]]) -> str | None:
    """
    Check if any time-tagged column's synonym appears literally in the query.
    If so, return that column's ref.  This is priority (a).
    """
    query_lower = query.lower()
    for tc in time_columns:
        for syn in tc["synonyms"]:
            if syn in query_lower:
                return tc["ref"]
    return None


def _resolve_column(
    query: str,
    linked_tables: list[str],
    time_columns: list[dict[str, Any]],
) -> tuple[str | None, str]:
    """
    Apply the three-tier priority for column selection.

    Returns (column_ref_or_None, resolution_label).
    """
    # (a) Anchor word in query matches a time-tagged column synonym
    anchor = _find_anchor_column(query, time_columns)
    if anchor:
        return anchor, "anchor"

    # (b) One linked table with a default_time_column
    candidates: list[str] = []
    for table in linked_tables:
        dtc = get_default_time_column(table)
        if dtc:
            candidates.append(dtc)

    # Also consider tables that own time columns in the schema even if
    # they weren't explicitly linked (the linker may have matched a column
    # from that table).
    # But stick to the spec: use linked_tables only.

    if len(candidates) == 1:
        return candidates[0], "default_column"

    if len(candidates) == 0:
        # No table has a time column — no time filter applicable
        return None, "default_column"

    # (c) Ambiguous — multiple linked tables with time columns
    return None, "ambiguous"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def resolve_time_filter(
    query: str,
    linked_tables: list[str],
    reference_date: str | None = None,
) -> dict[str, Any] | None:
    """
    Detect and resolve time phrases in *query*.

    Parameters
    ----------
    query : str
        The user's natural-language question.
    linked_tables : list[str]
        Table names already linked by the schema linker.
    reference_date : str, optional
        "Today" in YYYY-MM-DD format (default: config.REFERENCE_DATE).

    Returns
    -------
    dict | None
        ``{column, range, resolution}`` if a time phrase is found, else None.
        *column* may be None when resolution == "ambiguous".
    """
    ref_str = reference_date or REFERENCE_DATE
    ref_dt = _parse_reference_date(ref_str)

    # dateparser settings: use the reference date as "today"
    settings: dict[str, Any] = {
        "RELATIVE_BASE": ref_dt,
        "PREFER_DATES_FROM": "past",
    }

    # search_dates returns list of (phrase, datetime) or None
    found = search_dates(query, settings=settings)

    if not found:
        return None

    # Use the first detected time phrase
    phrase, resolved_dt = found[0]

    # Compute the date range
    date_range = _infer_range(phrase, resolved_dt, ref_dt)

    # Resolve which column to attach the filter to
    time_columns = get_time_tagged_columns()
    column, resolution = _resolve_column(query, linked_tables, time_columns)

    return {
        "column": column,
        "range": date_range,
        "resolution": resolution,
    }
