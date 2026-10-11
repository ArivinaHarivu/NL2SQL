"""Tests for ir/models.py — Pydantic IR model validation."""

import pytest
from pydantic import ValidationError

from nl2sql_ir.ir.models import FilterSpec, MetricSpec, QueryIR, TimeFilter


class TestMetricSpec:
    """MetricSpec validation rules."""

    def test_valid_metric(self):
        m = MetricSpec(operation="SUM", column="orders.o_totalprice")
        assert m.operation == "SUM"
        assert m.column == "orders.o_totalprice"

    def test_empty_column_rejected(self):
        with pytest.raises(ValidationError):
            MetricSpec(operation="COUNT", column="")

    def test_whitespace_column_rejected(self):
        with pytest.raises(ValidationError):
            MetricSpec(operation="AVG", column="   ")

    def test_invalid_operation_rejected(self):
        with pytest.raises(ValidationError):
            MetricSpec(operation="MEDIAN", column="orders.o_totalprice")


class TestQueryIR:
    """QueryIR model-level validators."""

    def test_valid_ir_passes(self):
        ir = QueryIR(
            entities=["orders"],
            metric=MetricSpec(operation="SUM", column="orders.o_totalprice"),
            filters=[],
            group_by=["orders.o_orderpriority"],
            having=FilterSpec(field="SUM(orders.o_totalprice)", op=">", value=1000),
        )
        assert ir.entities == ["orders"]

    def test_having_without_group_by_fails(self):
        """HAVING without GROUP BY must raise a validation error."""
        with pytest.raises(ValidationError, match="HAVING"):
            QueryIR(
                entities=["orders"],
                metric=MetricSpec(operation="COUNT", column="orders.o_orderkey"),
                filters=[],
                group_by=[],
                having=FilterSpec(field="COUNT(orders.o_orderkey)", op=">", value=5),
            )

    def test_aggregate_without_group_by_is_ok(self):
        """Aggregate over the whole result set (no group_by) is valid."""
        ir = QueryIR(
            entities=["orders", "customer"],
            metric=MetricSpec(operation="AVG", column="orders.o_totalprice"),
            filters=[],
            group_by=[],
        )
        assert ir.metric is not None
        assert ir.group_by == []

    def test_time_filter_round_trips(self):
        tf = TimeFilter(
            column="orders.o_orderdate",
            range=("1995-01-01", "1995-12-31"),
            resolution="default_column",
        )
        ir = QueryIR(entities=["orders"], time_filter=tf)
        assert ir.time_filter.column == "orders.o_orderdate"
        assert ir.time_filter.resolution == "default_column"

    def test_ambiguous_time_filter(self):
        tf = TimeFilter(column=None, range=("1996-01-01", "1996-12-31"), resolution="ambiguous")
        ir = QueryIR(entities=["orders", "lineitem"], time_filter=tf)
        assert ir.time_filter.column is None
        assert ir.time_filter.resolution == "ambiguous"

    def test_subquery_nesting(self):
        sub = QueryIR(
            entities=["orders"],
            metric=MetricSpec(operation="AVG", column="orders.o_totalprice"),
        )
        outer = QueryIR(
            entities=["customer", "orders"],
            metric=MetricSpec(operation="SUM", column="orders.o_totalprice"),
            group_by=["customer.c_custkey"],
            subquery=sub,
        )
        assert outer.subquery is not None
        assert outer.subquery.metric.operation == "AVG"

    def test_unsupported_flag(self):
        ir = QueryIR(unsupported=True)
        assert ir.unsupported is True

    def test_confidence_dict(self):
        ir = QueryIR(
            entities=["orders"],
            confidence={"entities": 1.0, "metric": 0.67},
        )
        assert ir.confidence["entities"] == 1.0
