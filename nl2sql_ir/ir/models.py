"""
Pydantic v2 models for the Query Intermediate Representation (IR).

The IR is entity-centric with implicit joins.  Join keys / join types are
NOT part of this representation — they are resolved later from the schema
graph.  Subqueries are used only for genuinely nested semantics.
"""

from __future__ import annotations

from typing import Any, Literal, Optional, Union

from pydantic import BaseModel, Field, field_validator, model_validator


class MetricSpec(BaseModel):
    """A single aggregate metric requested by the user."""

    operation: Literal["SUM", "COUNT", "AVG", "MIN", "MAX"]
    column: str

    @field_validator("column")
    @classmethod
    def column_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("MetricSpec.column must not be empty when a metric is present")
        return v


class FilterSpec(BaseModel):
    """A scalar filter predicate."""

    field: str
    op: Literal["=", "!=", ">", "<", ">=", "<="]
    value: Union[str, float, int]


class TimeFilter(BaseModel):
    """Resolved time-range filter with resolution provenance."""

    column: Optional[str] = None
    range: Optional[tuple[str, str]] = None
    resolution: Literal["anchor", "default_column", "ambiguous"]


class QueryIR(BaseModel):
    """
    Top-level Intermediate Representation for a natural-language query.

    Entity-centric: *entities* lists the schema tables involved.
    Joins are implicit and resolved downstream from the schema graph.
    """

    entities: list[str] = Field(default_factory=list)
    metric: Optional[MetricSpec] = None
    filters: list[FilterSpec] = Field(default_factory=list)
    time_filter: Optional[TimeFilter] = None
    group_by: list[str] = Field(default_factory=list)
    having: Optional[FilterSpec] = None
    order_by: Optional[dict[str, Any]] = None
    limit: Optional[int] = None

    # Self-referential for genuinely nested queries only
    subquery: Optional["QueryIR"] = None

    # Per-field confidence from self-consistency voting (populated by extractor)
    confidence: dict[str, float] = Field(default_factory=dict)

    # Flag when the question cannot be mapped at all
    unsupported: bool = False

    @model_validator(mode="after")
    def having_requires_group_by(self) -> "QueryIR":
        """HAVING without GROUP BY is invalid SQL — reject early."""
        if self.having is not None and not self.group_by:
            raise ValueError(
                "HAVING clause requires a non-empty group_by list.  "
                "Got having={!r} but group_by is empty.".format(self.having)
            )
        return self


# Rebuild model to register the self-referential type
QueryIR.model_rebuild()
