"""
Central configuration for the NL2SQL IR extraction stage.
All configurable constants live here — no hardcoded values in other modules.
"""

# ---------------------------------------------------------------------------
# Ollama / LLM settings
# ---------------------------------------------------------------------------
OLLAMA_MODEL: str = "qwen2.5:7b"
"""Name of the local Ollama model used for IR extraction."""

OLLAMA_BASE_URL: str = "http://localhost:11434"
"""Base URL for the Ollama server."""

# ---------------------------------------------------------------------------
# Self-consistency settings
# ---------------------------------------------------------------------------
SELF_CONSISTENCY_N: int = 3
"""Number of extraction runs per question for self-consistency voting."""

# ---------------------------------------------------------------------------
# Schema linker settings
# ---------------------------------------------------------------------------
FUZZY_MATCH_THRESHOLD: int = 75
"""Minimum rapidfuzz WRatio score to accept a schema-element match."""

# ---------------------------------------------------------------------------
# Time resolver settings
# ---------------------------------------------------------------------------
REFERENCE_DATE: str = "1998-08-02"
"""
Default reference date for resolving relative time phrases.

This represents a realistic "today" for the TPC-H dataset. In a live system
this should be sourced from MAX(o_orderdate) in the database. Here it is a
constant so the module works without a DB connection.
"""

# ---------------------------------------------------------------------------
# Few-shot / embedding settings
# ---------------------------------------------------------------------------
EMBEDDING_MODEL: str = "all-MiniLM-L6-v2"
"""Sentence-transformers model used to embed few-shot examples."""

FEWSHOT_TOP_K: int = 4
"""Number of few-shot examples to retrieve per query."""

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
import pathlib as _pathlib

PACKAGE_DIR: _pathlib.Path = _pathlib.Path(__file__).resolve().parent
SCHEMA_GRAPH_PATH: _pathlib.Path = PACKAGE_DIR / "schema" / "schema_graph.json"
LABELED_EXAMPLES_PATH: _pathlib.Path = PACKAGE_DIR / "eval" / "labeled_examples.json"
