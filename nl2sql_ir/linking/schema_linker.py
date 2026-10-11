"""
Fuzzy-matches query text against flattened schema terms using rapidfuzz.

Generates candidate n-grams (1–3), scores each against the schema term list
with WRatio, deduplicates overlapping matches, and returns ranked results.
"""

from __future__ import annotations

import re
from typing import Any

from rapidfuzz import fuzz, process

from nl2sql_ir.config import FUZZY_MATCH_THRESHOLD
from nl2sql_ir.schema.schema_loader import get_schema_terms


def _tokenize(query: str) -> list[str]:
    """Split query into lowercase tokens, stripping punctuation."""
    return re.findall(r"[a-z0-9_]+", query.lower())


def _generate_ngrams(tokens: list[str], max_n: int = 3) -> list[tuple[str, int, int]]:
    """
    Generate n-grams of length 1..max_n from *tokens*.

    Returns: list of (ngram_text, start_token_idx, end_token_idx).
    """
    ngrams: list[tuple[str, int, int]] = []
    for n in range(1, min(max_n, len(tokens)) + 1):
        for i in range(len(tokens) - n + 1):
            gram = " ".join(tokens[i : i + n])
            ngrams.append((gram, i, i + n - 1))
    return ngrams


def _deduplicate(
    matches: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Remove overlapping matches, preferring longer n-gram spans and higher
    scores.  Two matches overlap if their token ranges intersect.
    """
    # Sort: longer span first, then higher score
    sorted_matches = sorted(
        matches,
        key=lambda m: (m["_end"] - m["_start"], m["score"]),
        reverse=True,
    )

    taken_indices: set[int] = set()
    deduped: list[dict[str, Any]] = []

    for m in sorted_matches:
        span = set(range(m["_start"], m["_end"] + 1))
        if span & taken_indices:
            continue
        taken_indices |= span
        deduped.append(m)

    return deduped


def link_schema(
    query: str,
    threshold: int | None = None,
    schema_terms: list[dict[str, str]] | None = None,
) -> list[dict[str, Any]]:
    """
    Match natural-language *query* against the TPC-H schema.

    Parameters
    ----------
    query : str
        The user's natural-language question.
    threshold : int, optional
        Minimum WRatio score to accept (default: config.FUZZY_MATCH_THRESHOLD).
    schema_terms : list[dict], optional
        Override the schema terms (useful for testing).

    Returns
    -------
    list[dict]
        Ranked list of matches, each with keys:
        ``matched_text, schema_term, ref, type, score``.
    """
    if threshold is None:
        threshold = FUZZY_MATCH_THRESHOLD

    terms = schema_terms or get_schema_terms()
    term_strings = [t["term"] for t in terms]

    tokens = _tokenize(query)
    if not tokens:
        return []

    ngrams = _generate_ngrams(tokens)

    raw_matches: list[dict[str, Any]] = []

    for gram, start, end in ngrams:
        result = process.extractOne(
            gram,
            term_strings,
            scorer=fuzz.WRatio,
            score_cutoff=threshold,
        )
        if result is None:
            continue

        matched_term, score, idx = result
        entry = terms[idx]

        raw_matches.append({
            "matched_text": gram,
            "schema_term": entry["term"],
            "ref": entry["ref"],
            "type": entry["type"],
            "score": score,
            "_start": start,
            "_end": end,
        })

    deduped = _deduplicate(raw_matches)

    # Strip internal bookkeeping keys and sort by score descending
    for m in deduped:
        m.pop("_start", None)
        m.pop("_end", None)

    deduped.sort(key=lambda m: m["score"], reverse=True)
    return deduped
