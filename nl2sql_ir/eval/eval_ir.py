#!/usr/bin/env python
"""
Evaluation harness for the IR extraction stage.

Loads labeled_examples.json, runs extract_ir on each question, compares
the output field-by-field against the expected IR, and prints per-field
accuracy plus overall exact-match accuracy.

Usage:
    python -m nl2sql_ir.eval.eval_ir
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from nl2sql_ir.config import LABELED_EXAMPLES_PATH


# Fields we compare (excludes 'confidence' which is computed, not expected)
_COMPARED_FIELDS = [
    "entities", "metric", "filters", "time_filter",
    "group_by", "having", "order_by", "limit", "subquery", "unsupported",
]


def _normalize(value: Any) -> str:
    """Deterministic string representation for comparison."""
    if value is None:
        return "__NONE__"
    if isinstance(value, (list, dict, tuple)):
        return json.dumps(value, sort_keys=True, default=str)
    return str(value)


def _load_examples(path: Path | None = None) -> list[dict[str, Any]]:
    path = path or LABELED_EXAMPLES_PATH
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data["examples"]


def evaluate(examples_path: Path | None = None) -> dict[str, Any]:
    """
    Run the full evaluation and return structured results.

    Returns
    -------
    dict with keys:
      - field_accuracy: {field_name: float}
      - exact_match_accuracy: float
      - per_example: list of per-example result dicts
    """
    # Import here so the module can be loaded without Ollama running
    from nl2sql_ir.ir.extractor import extract_ir

    examples = _load_examples(examples_path)
    total = len(examples)

    field_correct: dict[str, int] = {f: 0 for f in _COMPARED_FIELDS}
    exact_matches = 0
    per_example: list[dict[str, Any]] = []

    for i, ex in enumerate(examples):
        question = ex["question"]
        expected = ex["expected_ir"]

        print(f"\n[{i + 1}/{total}] {question}")
        try:
            ir = extract_ir(question)
            predicted = ir.model_dump()
        except Exception as exc:
            print(f"  ERROR: {exc}")
            per_example.append({"question": question, "error": str(exc)})
            continue

        all_match = True
        field_results: dict[str, bool] = {}

        for field in _COMPARED_FIELDS:
            expected_val = expected.get(field)
            predicted_val = predicted.get(field)

            match = _normalize(expected_val) == _normalize(predicted_val)
            field_results[field] = match
            if match:
                field_correct[field] += 1
            else:
                all_match = False
                print(f"  MISMATCH [{field}]:")
                print(f"    expected:  {_normalize(expected_val)}")
                print(f"    predicted: {_normalize(predicted_val)}")

        if all_match:
            exact_matches += 1
            print("  ✓ exact match")

        per_example.append({
            "question": question,
            "field_results": field_results,
            "exact_match": all_match,
        })

    # Compute accuracies
    field_accuracy = {
        f: round(field_correct[f] / total, 4) if total > 0 else 0.0
        for f in _COMPARED_FIELDS
    }
    exact_match_accuracy = round(exact_matches / total, 4) if total > 0 else 0.0

    return {
        "field_accuracy": field_accuracy,
        "exact_match_accuracy": exact_match_accuracy,
        "per_example": per_example,
    }


def main() -> None:
    results = evaluate()

    print("\n" + "=" * 60)
    print("PER-FIELD ACCURACY")
    print("=" * 60)
    for field, acc in results["field_accuracy"].items():
        bar = "█" * int(acc * 20) + "░" * (20 - int(acc * 20))
        print(f"  {field:18s}  {bar}  {acc * 100:5.1f}%")

    print(f"\n  {'EXACT MATCH':18s}  {'':20s}  {results['exact_match_accuracy'] * 100:5.1f}%")
    print("=" * 60)


if __name__ == "__main__":
    main()
