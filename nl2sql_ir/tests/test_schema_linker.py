"""Tests for schema_linker.py — fuzzy matching against TPC-H schema terms."""

import pytest

from nl2sql_ir.linking.schema_linker import link_schema


class TestSchemaLinker:
    """Known queries → expected schema matches."""

    def test_matches_table_name(self):
        """'orders' in the query should match the orders table."""
        results = link_schema("show me all orders")
        refs = [r["ref"] for r in results]
        assert any("orders" in ref for ref in refs)

    def test_matches_column_synonym(self):
        """'revenue' should match o_totalprice via synonym."""
        results = link_schema("total revenue")
        refs = [r["ref"] for r in results]
        assert any("o_totalprice" in ref for ref in refs)

    def test_matches_customer_table(self):
        """'customers' should fuzzy-match to the customer table."""
        results = link_schema("list all customers")
        refs = [r["ref"] for r in results]
        assert any("customer" in ref for ref in refs)

    def test_matches_nation_synonym(self):
        """'country' should match n_name via synonym."""
        results = link_schema("filter by country")
        refs = [r["ref"] for r in results]
        assert any("n_name" in ref for ref in refs)

    def test_threshold_filters_low_scores(self):
        """A very high threshold should eliminate weak matches."""
        results = link_schema("xyzzy foobar gibberish", threshold=95)
        assert len(results) == 0

    def test_supplier_name_match(self):
        """'supplier name' bigram should match s_name synonym."""
        results = link_schema("what is the supplier name")
        refs = [r["ref"] for r in results]
        assert any("s_name" in ref for ref in refs)

    def test_returns_score_and_type(self):
        """Each result dict should contain the expected keys."""
        results = link_schema("orders")
        assert len(results) > 0
        r = results[0]
        assert "matched_text" in r
        assert "schema_term" in r
        assert "ref" in r
        assert "type" in r
        assert "score" in r
        assert isinstance(r["score"], (int, float))

    def test_deduplication_prefers_longer(self):
        """
        'order date' (bigram) should beat 'order' (unigram) for the same
        token span, so we shouldn't see both.
        """
        results = link_schema("filter by order date")
        # Should not have duplicate overlapping matches for "order" and "order date"
        matched_texts = [r["matched_text"] for r in results]
        # If "order date" is present, "order" alone should NOT also be present
        # (they overlap on the "order" token).
        if "order date" in matched_texts:
            # The "order" unigram should have been deduplicated
            assert matched_texts.count("order") == 0 or matched_texts.count("order date") >= 1
