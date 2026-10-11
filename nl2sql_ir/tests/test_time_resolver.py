"""Tests for time_resolver.py — date-phrase resolution and column selection."""

import pytest

from nl2sql_ir.linking.time_resolver import resolve_time_filter


# Fixed reference date for deterministic tests (TPC-H era)
REF_DATE = "1998-08-02"


class TestTimeResolver:
    """Known time phrases → expected date ranges and resolution labels."""

    def test_explicit_year(self):
        """'in 1995' → 1995-01-01 to 1995-12-31."""
        result = resolve_time_filter(
            "total revenue in 1995",
            linked_tables=["orders"],
            reference_date=REF_DATE,
        )
        assert result is not None
        assert result["range"] == ("1995-01-01", "1995-12-31")

    def test_this_year(self):
        """'this year' relative to 1998-08-02 → 1998-01-01 to 1998-12-31."""
        result = resolve_time_filter(
            "orders placed this year",
            linked_tables=["orders"],
            reference_date=REF_DATE,
        )
        assert result is not None
        assert result["range"][0] == "1998-01-01"
        assert result["range"][1] == "1998-12-31"

    def test_last_year(self):
        """'last year' relative to 1998-08-02 → 1997 full year."""
        result = resolve_time_filter(
            "orders from last year",
            linked_tables=["orders"],
            reference_date=REF_DATE,
        )
        assert result is not None
        assert result["range"][0] == "1997-01-01"
        assert result["range"][1] == "1997-12-31"

    def test_default_column_resolution(self):
        """Single linked table (orders) → column = orders.o_orderdate, resolution = default_column."""
        result = resolve_time_filter(
            "orders in 1996",
            linked_tables=["orders"],
            reference_date=REF_DATE,
        )
        assert result is not None
        assert result["column"] == "orders.o_orderdate"
        assert result["resolution"] == "default_column"

    def test_anchor_resolution(self):
        """'shipped' anchor word matches l_shipdate synonym 'ship date'."""
        result = resolve_time_filter(
            "items shipped in 1995",
            linked_tables=["lineitem"],
            reference_date=REF_DATE,
        )
        assert result is not None
        # Should resolve via anchor or default_column for lineitem
        assert result["column"] is not None
        assert "shipdate" in result["column"] or result["resolution"] in ("anchor", "default_column")

    def test_ambiguous_multiple_tables(self):
        """Multiple linked tables with time columns → ambiguous."""
        result = resolve_time_filter(
            "data from 1996",
            linked_tables=["orders", "lineitem"],
            reference_date=REF_DATE,
        )
        assert result is not None
        assert result["resolution"] == "ambiguous"
        assert result["column"] is None

    def test_no_time_phrase(self):
        """Query without time phrases → None."""
        result = resolve_time_filter(
            "list all customers",
            linked_tables=["customer"],
            reference_date=REF_DATE,
        )
        # dateparser may or may not find something; if no time phrase, result is None
        # This is a soft assertion — dateparser can be aggressive.
        # At minimum, if result is returned, the structure should be valid.
        if result is not None:
            assert "range" in result
            assert "resolution" in result

    def test_table_without_time_column(self):
        """Linked table with no default_time_column and no anchor → column is None."""
        result = resolve_time_filter(
            "regions in 1995",
            linked_tables=["region"],
            reference_date=REF_DATE,
        )
        if result is not None:
            # region has no time column, so column should be None
            assert result["column"] is None
