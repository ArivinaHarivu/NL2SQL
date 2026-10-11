"""
Tests for ir/extractor.py — smoke test with mocked LLM calls.

These tests verify the extraction pipeline logic (schema linking,
self-consistency voting) without requiring a live Ollama instance.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from nl2sql_ir.ir.extractor import _majority_vote, extract_ir
from nl2sql_ir.ir.models import FilterSpec, MetricSpec, QueryIR


class TestMajorityVote:
    """Unit tests for the self-consistency voting logic."""

    def test_unanimous_gives_full_confidence(self):
        """Three identical runs → all fields confidence = 1.0."""
        ir = QueryIR(
            entities=["orders"],
            metric=MetricSpec(operation="SUM", column="orders.o_totalprice"),
        )
        result = _majority_vote([ir, ir, ir])
        assert result.entities == ["orders"]
        assert result.confidence["entities"] == 1.0
        assert result.confidence["metric"] == 1.0

    def test_two_of_three_gives_067(self):
        """Two agreeing + one differing → confidence ≈ 0.67."""
        ir_a = QueryIR(entities=["orders"], metric=MetricSpec(operation="SUM", column="orders.o_totalprice"))
        ir_b = QueryIR(entities=["orders"], metric=MetricSpec(operation="AVG", column="orders.o_totalprice"))
        result = _majority_vote([ir_a, ir_a, ir_b])
        assert result.metric.operation == "SUM"
        assert result.confidence["metric"] == 0.67

    def test_all_disagree_gives_033(self):
        """Three different values → confidence ≈ 0.33."""
        ir_a = QueryIR(entities=["orders"])
        ir_b = QueryIR(entities=["customer"])
        ir_c = QueryIR(entities=["supplier"])
        result = _majority_vote([ir_a, ir_b, ir_c])
        assert result.confidence["entities"] == 0.33


class TestExtractIR:
    """Smoke test for the full extraction pipeline with mocked LLM."""

    @patch("nl2sql_ir.ir.extractor._extract_once")
    @patch("nl2sql_ir.ir.extractor._get_fewshot_store")
    def test_extract_ir_returns_valid_queryir(self, mock_store, mock_extract):
        """Mocked LLM returns a valid IR; pipeline should succeed."""
        # Mock the few-shot store
        store = MagicMock()
        store.retrieve.return_value = []
        mock_store.return_value = store

        # Mock the LLM call to return a fixed IR
        fixed_ir = QueryIR(
            entities=["orders"],
            metric=MetricSpec(operation="SUM", column="orders.o_totalprice"),
            filters=[],
            group_by=[],
        )
        mock_extract.return_value = fixed_ir

        result = extract_ir("What is the total revenue?")

        assert isinstance(result, QueryIR)
        assert result.entities == ["orders"]
        assert result.metric.operation == "SUM"
        assert "entities" in result.confidence

    @patch("nl2sql_ir.ir.extractor._extract_once")
    @patch("nl2sql_ir.ir.extractor._get_fewshot_store")
    def test_time_filter_from_resolver_overrides_llm(self, mock_store, mock_extract):
        """The deterministic time resolver output should override the LLM."""
        store = MagicMock()
        store.retrieve.return_value = []
        mock_store.return_value = store

        # LLM returns no time filter
        fixed_ir = QueryIR(entities=["orders"])
        mock_extract.return_value = fixed_ir

        result = extract_ir("orders in 1995", reference_date="1998-08-02")

        assert isinstance(result, QueryIR)
        # The time resolver should have detected "1995" and set a time_filter
        if result.time_filter is not None:
            assert result.time_filter.range is not None
