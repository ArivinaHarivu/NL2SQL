"""
Few-shot example store for the IR extraction prompt.

Uses sentence-transformers embeddings + in-memory cosine similarity.
The interface is intentionally small so it can be swapped for pgvector later.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

import numpy as np

from nl2sql_ir.config import EMBEDDING_MODEL


# ---------------------------------------------------------------------------
# Abstract interface (swap-friendly)
# ---------------------------------------------------------------------------

class FewShotStore(ABC):
    """Minimal interface for a few-shot example store."""

    @abstractmethod
    def add(self, question: str, ir: dict[str, Any]) -> None:
        """Store a (question, IR) pair."""

    @abstractmethod
    def retrieve(self, query: str, k: int = 4) -> list[dict[str, Any]]:
        """Return the *k* most similar examples to *query*."""


# ---------------------------------------------------------------------------
# In-memory implementation
# ---------------------------------------------------------------------------

class InMemoryFewShotStore(FewShotStore):
    """
    Embeds questions with sentence-transformers and retrieves via cosine
    similarity over a numpy array.  Sufficient for the prototype — swap
    for a pgvector-backed store when scaling.
    """

    def __init__(self, model_name: str | None = None) -> None:
        from sentence_transformers import SentenceTransformer

        self._model = SentenceTransformer(model_name or EMBEDDING_MODEL)
        self._questions: list[str] = []
        self._irs: list[dict[str, Any]] = []
        self._vectors: np.ndarray | None = None  # shape: (N, dim)

    # -- public API --------------------------------------------------------

    def add(self, question: str, ir: dict[str, Any]) -> None:
        vec = self._model.encode([question])  # (1, dim)
        self._questions.append(question)
        self._irs.append(ir)

        if self._vectors is None:
            self._vectors = np.array(vec)
        else:
            self._vectors = np.vstack([self._vectors, vec])

    def retrieve(self, query: str, k: int = 4) -> list[dict[str, Any]]:
        if self._vectors is None or len(self._questions) == 0:
            return []

        q_vec = self._model.encode([query])  # (1, dim)
        sims = self._cosine_similarity(q_vec, self._vectors).flatten()

        top_k = min(k, len(sims))
        indices = np.argsort(sims)[::-1][:top_k]

        return [
            {"question": self._questions[i], "ir": self._irs[i]}
            for i in indices
        ]

    # -- internals ---------------------------------------------------------

    @staticmethod
    def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> np.ndarray:
        """Cosine similarity between a (1, d) and b (N, d)."""
        a_norm = a / (np.linalg.norm(a, axis=1, keepdims=True) + 1e-10)
        b_norm = b / (np.linalg.norm(b, axis=1, keepdims=True) + 1e-10)
        return a_norm @ b_norm.T


# ---------------------------------------------------------------------------
# Seed examples — 12 hand-written (question, IR) pairs
# ---------------------------------------------------------------------------

SEED_EXAMPLES: list[dict[str, Any]] = [
    # 1. Simple filter
    {
        "question": "Show all orders with status F",
        "ir": {
            "entities": ["orders"],
            "metric": None,
            "filters": [{"field": "orders.o_orderstatus", "op": "=", "value": "F"}],
            "time_filter": None,
            "group_by": [],
            "having": None,
            "order_by": None,
            "limit": None,
            "subquery": None,
            "confidence": {},
            "unsupported": False,
        },
    },
    # 2. Simple aggregation
    {
        "question": "What is the total revenue from all orders?",
        "ir": {
            "entities": ["orders"],
            "metric": {"operation": "SUM", "column": "orders.o_totalprice"},
            "filters": [],
            "time_filter": None,
            "group_by": [],
            "having": None,
            "order_by": None,
            "limit": None,
            "subquery": None,
            "confidence": {},
            "unsupported": False,
        },
    },
    # 3. Top-N
    {
        "question": "Top 10 customers by account balance",
        "ir": {
            "entities": ["customer"],
            "metric": None,
            "filters": [],
            "time_filter": None,
            "group_by": [],
            "having": None,
            "order_by": {"column": "customer.c_acctbal", "direction": "DESC"},
            "limit": 10,
            "subquery": None,
            "confidence": {},
            "unsupported": False,
        },
    },
    # 4. Join-implying (two entities)
    {
        "question": "List suppliers in France",
        "ir": {
            "entities": ["supplier", "nation"],
            "metric": None,
            "filters": [{"field": "nation.n_name", "op": "=", "value": "FRANCE"}],
            "time_filter": None,
            "group_by": [],
            "having": None,
            "order_by": None,
            "limit": None,
            "subquery": None,
            "confidence": {},
            "unsupported": False,
        },
    },
    # 5. Time-range filter
    {
        "question": "Total revenue from orders in 1995",
        "ir": {
            "entities": ["orders"],
            "metric": {"operation": "SUM", "column": "orders.o_totalprice"},
            "filters": [],
            "time_filter": {
                "column": "orders.o_orderdate",
                "range": ["1995-01-01", "1995-12-31"],
                "resolution": "default_column",
            },
            "group_by": [],
            "having": None,
            "order_by": None,
            "limit": None,
            "subquery": None,
            "confidence": {},
            "unsupported": False,
        },
    },
    # 6. Group-by
    {
        "question": "Count orders by order priority",
        "ir": {
            "entities": ["orders"],
            "metric": {"operation": "COUNT", "column": "orders.o_orderkey"},
            "filters": [],
            "time_filter": None,
            "group_by": ["orders.o_orderpriority"],
            "having": None,
            "order_by": None,
            "limit": None,
            "subquery": None,
            "confidence": {},
            "unsupported": False,
        },
    },
    # 7. Group-by with HAVING
    {
        "question": "Nations with more than 100 suppliers",
        "ir": {
            "entities": ["supplier", "nation"],
            "metric": {"operation": "COUNT", "column": "supplier.s_suppkey"},
            "filters": [],
            "time_filter": None,
            "group_by": ["nation.n_name"],
            "having": {"field": "COUNT(supplier.s_suppkey)", "op": ">", "value": 100},
            "order_by": None,
            "limit": None,
            "subquery": None,
            "confidence": {},
            "unsupported": False,
        },
    },
    # 8. Two entities, aggregation
    {
        "question": "Average order value per customer",
        "ir": {
            "entities": ["orders", "customer"],
            "metric": {"operation": "AVG", "column": "orders.o_totalprice"},
            "filters": [],
            "time_filter": None,
            "group_by": ["customer.c_custkey"],
            "having": None,
            "order_by": None,
            "limit": None,
            "subquery": None,
            "confidence": {},
            "unsupported": False,
        },
    },
    # 9. Subquery — genuinely nested
    {
        "question": "Customers who spent more than the average order total",
        "ir": {
            "entities": ["customer", "orders"],
            "metric": {"operation": "SUM", "column": "orders.o_totalprice"},
            "filters": [],
            "time_filter": None,
            "group_by": ["customer.c_custkey"],
            "having": None,
            "order_by": None,
            "limit": None,
            "subquery": {
                "entities": ["orders"],
                "metric": {"operation": "AVG", "column": "orders.o_totalprice"},
                "filters": [],
                "time_filter": None,
                "group_by": [],
                "having": None,
                "order_by": None,
                "limit": None,
                "subquery": None,
                "confidence": {},
                "unsupported": False,
            },
            "confidence": {},
            "unsupported": False,
        },
    },
    # 10. Ambiguous time (multiple tables)
    {
        "question": "Total quantity shipped last year by supplier",
        "ir": {
            "entities": ["lineitem", "supplier"],
            "metric": {"operation": "SUM", "column": "lineitem.l_quantity"},
            "filters": [],
            "time_filter": {
                "column": "lineitem.l_shipdate",
                "range": ["1997-01-01", "1997-12-31"],
                "resolution": "anchor",
            },
            "group_by": ["supplier.s_name"],
            "having": None,
            "order_by": None,
            "limit": None,
            "subquery": None,
            "confidence": {},
            "unsupported": False,
        },
    },
    # 11. MIN metric
    {
        "question": "What is the cheapest supply cost?",
        "ir": {
            "entities": ["partsupp"],
            "metric": {"operation": "MIN", "column": "partsupp.ps_supplycost"},
            "filters": [],
            "time_filter": None,
            "group_by": [],
            "having": None,
            "order_by": None,
            "limit": None,
            "subquery": None,
            "confidence": {},
            "unsupported": False,
        },
    },
    # 12. Filter + time + limit
    {
        "question": "Top 5 urgent orders placed in 1996",
        "ir": {
            "entities": ["orders"],
            "metric": None,
            "filters": [{"field": "orders.o_orderpriority", "op": "=", "value": "1-URGENT"}],
            "time_filter": {
                "column": "orders.o_orderdate",
                "range": ["1996-01-01", "1996-12-31"],
                "resolution": "default_column",
            },
            "group_by": [],
            "having": None,
            "order_by": {"column": "orders.o_totalprice", "direction": "DESC"},
            "limit": 5,
            "subquery": None,
            "confidence": {},
            "unsupported": False,
        },
    },
]


def build_seeded_store(model_name: str | None = None) -> InMemoryFewShotStore:
    """Create an InMemoryFewShotStore pre-loaded with the seed examples."""
    store = InMemoryFewShotStore(model_name=model_name)
    for ex in SEED_EXAMPLES:
        store.add(ex["question"], ex["ir"])
    return store
