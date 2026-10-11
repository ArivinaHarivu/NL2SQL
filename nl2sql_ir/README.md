# NL2SQL IR — Text-to-IR Extraction Stage

This package implements one isolated stage of a voice-driven NL-to-SQL system:
**natural-language question → validated structured JSON Intermediate Representation (IR)**.

The IR is the contract the rest of the pipeline depends on. This stage prioritises
correctness and validation over speed.

## Architecture

```
User question (text)
       │
       ├─→ Schema Linker (rapidfuzz fuzzy matching against TPC-H schema graph)
       ├─→ Time Resolver (dateparser — resolves "last year", "in 1996", etc.)
       ├─→ Few-Shot Store (sentence-transformers embeddings, in-memory cosine sim)
       │
       └─→ IR Extractor
              • Assembles prompt from linked schema elements + time info + few-shot examples
              • Calls local Ollama model N=3 times via instructor (Pydantic-validated output)
              • Self-consistency vote → per-field confidence scores
              • Returns a validated QueryIR
```

## Quick start

```bash
# Install dependencies
pip install -r nl2sql_ir/requirements.txt

# Make sure Ollama is running with a model pulled, e.g.:
ollama pull qwen2.5:7b

# Run the evaluation harness
python -m nl2sql_ir.eval.eval_ir

# Run unit tests (no Ollama required — LLM calls are mocked)
pytest nl2sql_ir/tests/ -v
```

## Configuration

All tunables live in [`nl2sql_ir/config.py`](nl2sql_ir/config.py):

| Constant              | Default           | Purpose                                    |
|-----------------------|-------------------|--------------------------------------------|
| `OLLAMA_MODEL`        | `qwen2.5:7b`      | Local model for IR extraction              |
| `OLLAMA_BASE_URL`     | `localhost:11434` | Ollama server address                      |
| `SELF_CONSISTENCY_N`  | `3`               | Number of extraction runs for voting       |
| `FUZZY_MATCH_THRESHOLD` | `75`            | Minimum rapidfuzz WRatio score             |
| `REFERENCE_DATE`      | `1998-08-02`      | "Today" for relative time phrases          |
| `EMBEDDING_MODEL`     | `all-MiniLM-L6-v2`| Sentence-transformers model for few-shot   |
| `FEWSHOT_TOP_K`       | `4`               | Number of few-shot examples to retrieve    |

## Explicitly out of scope

The following are **later stages** of the larger pipeline — not bugs or missing features:

- **SQL generation** — downstream of the IR
- **Query execution** — downstream of SQL generation
- **Policy retrieval** — between IR and SQL generation
- **ASR / speech input** — upstream of this stage
- **Clarification UI** — the IR flags ambiguity; the UI handles it later
- **Candidate selection / caching / dashboard** — downstream stages

## File structure

```
nl2sql_ir/
├── config.py                  # Central configuration
├── requirements.txt           # Python dependencies
├── schema/
│   ├── schema_graph.json      # Hand-written TPC-H schema graph (8 tables, ~60 columns)
│   └── schema_loader.py       # Loads & flattens schema for fuzzy matching
├── linking/
│   ├── schema_linker.py       # Fuzzy n-gram matching via rapidfuzz
│   └── time_resolver.py       # Date-phrase resolution via dateparser
├── ir/
│   ├── models.py              # Pydantic v2 IR models (QueryIR, MetricSpec, etc.)
│   ├── fewshot_store.py       # Embedding-based few-shot example retrieval
│   └── extractor.py           # Full extraction pipeline with self-consistency
├── eval/
│   ├── labeled_examples.json  # 5 hand-labeled eval pairs (expand to 30-40 for Phase 7)
│   └── eval_ir.py             # Evaluation harness — per-field & exact-match accuracy
└── tests/
    ├── test_schema_linker.py
    ├── test_time_resolver.py
    ├── test_ir_models.py
    └── test_extractor.py
```

## Evaluation

```bash
python -m nl2sql_ir.eval.eval_ir
```

Outputs per-field accuracy (entities, metric, filters, time_filter, group_by,
having, order_by, limit, subquery, unsupported) plus overall exact-match accuracy.
