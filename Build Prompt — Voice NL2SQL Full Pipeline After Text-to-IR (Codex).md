# Build Prompt — Voice NL2SQL: Full Pipeline After Text-to-IR (Codex)

Oct 11, 2026 · @Pranav Kulkarni

## 1. Role, goal, how to work

Paste this whole document into Codex as the task. It builds everything after the text-to-IR stage, which already exists in `nl2sql_ir/`.

**Role.** You are a senior Python engineer extending an existing package into a working, measurable, voice-driven NL-to-SQL system over PostgreSQL + TPC-H (scale factor 1).

**Goal.** A demonstrable system where a spoken or typed question becomes a validated IR, then SQL, then an executed and charted result, with honest clarification when the system is unsure. The deliverable is judged on measured results, not on claims. Every module must be testable without the others (inject dependencies, mock the LLM and ASR in unit tests).

**How to work.**

1. Read the whole prompt before writing code. Then work in the order in section 9, one module at a time.
2. For each module: write the interface and the tests first, then the implementation, then run the tests. Do not start the next module until the current tests pass.
3. After each module, print a three-line status: what exists, what passes, what is stubbed.
4. If a requirement here conflicts with something you find in the existing code, stop and report the conflict. Do not silently pick one.
5. Prefer plain Python and small functions. No LangChain, no LlamaIndex, no agent frameworks. Orchestration is an explicit function in `pipeline/orchestrator.py`.
6. Anything non-deterministic (LLM calls, ASR) sits behind a small interface class so it can be replaced by a fake in tests.

## 2. Context and non-negotiable decisions

**Pipeline, in order.** Input (voice or typed) → ASR → DB-aware correction → schema linking + time resolution (built) → IR extraction with self-consistency confidence (built) → policy retrieval → SQL generation (2–3 candidates) → code-level guardrails → execute all candidates and group by result equivalence → cost-based selection among equivalent candidates → result → execution cache → dashboard. Typed input skips ASR and goes through the identical path.

**Decisions that must not be changed without asking.**

- **Two clarification triggers.** (a) A low-confidence IR slot. (b) Candidate results disagree. Clarification is multiple-choice, never free text. Maximum 2 clarification rounds per question, then answer with the best candidate and a visible warning.
- **Safety is enforced in code, not only in the prompt.** Read-only DB role, `statement_timeout`, forced `LIMIT`, single-statement SELECT-only validation via an SQL parser. Prompt rules are an addition, not the defence.
- **Join order is the planner's job, not the LLM's.** PostgreSQL reorders inner joins freely (`join_collapse_limit = from_collapse_limit = 8`, `geqo_threshold = 12`); outer joins constrain reordering; a CTE referenced more than once is materialized by default. So: do **not** pass row statistics to the LLM, and do **not** claim the LLM optimizes join order. The LLM's only real lever is **query shape** (JOIN vs EXISTS vs pre-aggregated subquery vs CTE). Candidates must differ in shape, and cost-based selection compares plan cost only among candidates whose results are equivalent.
- **Cost selection is only a tie-breaker.** It never overrides result disagreement. If candidates disagree, that is a clarification, not a cost decision.
- **Execution cache is separate from the few-shot store.** The few-shot store (nearest validated question → IR pairs) is built in stage 1. The execution cache stores validated (question embedding → SQL + result metadata). Only validated entries are ever added.
- **Join paths come from the schema graph (BFS), never from the model.** The graph has two relation lists: `relations` (simple FKs) and `composite_relations` (lineitem → partsupp on `(l_partkey, l_suppkey)`). All join-path code reads both.
- **Time is relative to the dataset, not the wall clock.** TPC-H dates span about 1992–1998. "Last year" resolves against the dataset's max date (already implemented in `time_resolver`).
- **Fixed index set.** The index list in `tpch_keys.sql` is frozen for all evaluation runs. Changing indexes changes plans and invalidates comparisons.
- **Honest claims only.** The system must log, per question, which stages ran and which signals fired, so every number in the final report can be reproduced from logs.

## 3. Already built: do not rebuild

The package `nl2sql_ir/` exists and its tests pass. **First action: read it** (`config.py`, `schema/`, `linking/`, `ir/`, `eval/`) and confirm the contract below. If any signature differs, adapt your new code to the real one through a thin adapter in `pipeline/adapters.py`; do not edit stage-1 files except to fix a bug you can prove with a failing test.

| Existing piece | What you may rely on |
| --- | --- |
| `schema/schema_loader.py` | Loads `schema_graph.json` (8 tables, 61 columns, 9 `relations`, 1 `composite_relations` entry). Tags: identifier / metric / time / entity\_name / filterable. Synonyms per column. `default_time_column` per table. |
| `linking/schema_linker.py` | `link_schema(query, threshold=75)` returns ranked table/column candidates from rapidfuzz over names and synonyms. |
| `linking/time_resolver.py` | `resolve_time_filter(query, linked_tables, reference_date)`; dateparser with `RELATIVE_BASE` = dataset max date. Ambiguous time column is flagged, never guessed. |
| `ir/models.py` | Pydantic v2 `QueryIR`: entities, metric (`MetricSpec`), filters (`FilterSpec`), `time_filter` (`TimeFilter`), group\_by, having, order\_by, limit, `subquery`, `confidence` (per field: 1.0 / 0.67 / 0.33 from 3-run self-consistency), `unsupported` flag. |
| `ir/extractor.py` | `extract_ir(query) -> QueryIR` using Ollama + Instructor, 3 runs, per-field agreement. |
| `ir/fewshot_store.py` | `FewShotStore.add / retrieve`; `InMemoryFewShotStore` with all-MiniLM-L6-v2 and numpy cosine. |
| `eval/labeled_examples.json` | 30–40 hand-labeled (question → IR) examples. |

**Two things stage 1 does not do, which you must add on top of it:**

1. A **join-path resolver** (`schema/join_paths.py`): given the IR's entities, return the shortest join path from the schema graph by BFS, reading both `relations` and `composite_relations`. If stage 1 already has one, reuse it and only add the composite-FK handling and tests.
2. An **IR renderer** (`ir/render.py`): turn a `QueryIR` plus its resolved join paths into a compact, deterministic text block for the SQL-generation prompt (tables, join conditions written out, metric expression, filters, grouping, ordering, limit, nested subquery indented). Same IR in, same text out.

## 4. Repository layout

Add a sibling package `nl2sql_app/` next to `nl2sql_ir/`. It imports from `nl2sql_ir` and never the reverse. Create exactly this structure (extra helper files are fine, missing ones are not):

```text
nl2sql_app/
  config.py                  # DB DSNs, model names, thresholds, paths (env-driven)
  requirements.txt
  README.md                  # run instructions, how to reproduce every number
  db/
    setup_tpch.md            # load steps (see section 5)
    roles.sql                # read-only role, timeouts
    connection.py            # pool, read-only execution helper
  asr/
    transcriber.py           # faster-whisper wrapper -> Transcript
    models.py                # Transcript, WordInfo (text, start, end, probability)
  correction/
    db_vocab.py              # builds vocab from schema names + sampled values
    corrector.py             # rapidfuzz correction, returns edits + confidences
  policy/
    registry.json            # hand-written policies (patterns + heuristics)
    retriever.py             # rule lookup on IR fields, embedding fallback
  generation/
    prompts.py               # prompt templates (system + user), versioned
    generator.py             # N candidates via API, shape-diverse
    llm_client.py            # Anthropic/OpenAI behind one interface + FakeLLM
  guardrails/
    sql_validator.py         # sqlglot/pglast based SELECT-only checks
    enforce.py               # forced LIMIT, timeout, identifier allowlist
  execution/
    runner.py                # execute candidates safely, capture rows + timing
    equivalence.py           # canonicalize results, group candidates
    cost.py                  # EXPLAIN (ANALYZE, FORMAT JSON) parsing + selection
  clarification/
    signals.py               # per-source uncertainty signals
    policy.py                # when to ask, which question, merge answer
    memory.py                # (optional) learned thresholds, section 7
  cache/
    execution_cache.py       # pgvector-backed validated (question -> SQL) cache
  dashboard/
    chart_mapper.py          # deterministic result-shape -> chart spec
    app.py                   # Streamlit app
  pipeline/
    adapters.py
    orchestrator.py          # run_pipeline(input) -> PipelineResult
    trace.py                 # per-stage timing + signals, JSONL log
  eval/
    questions.json           # 100-150 questions with gold SQL + type tags
    run_baseline.py          # naive ASR -> LLM -> SQL
    run_full.py              # full pipeline
    join_order_invariance.py # planner experiment, section 8
    shape_cost_compare.py    # formulation cost comparison, section 8
    report.py                # tables + per-stage latency breakdown
  tests/                     # one test file per module
```

Also create `schema/join_paths.py` and `ir/render.py` inside `nl2sql_ir/` as described in section 3.

## 5. Database setup

The human loads TPC-H data; you write the scripts and docs, and verify what you can. Files already provided by the user: `schema_graph.json` (validated against TPC-H v3.0.1, 0 differences) and `tpch_keys.sql` (PKs, 9 simple FKs, the composite lineitem → partsupp FK, seven named indexes, `ANALYZE`). Use them as given.

**`db/setup_tpch.md`** documents the load, in this order:

1. Build [tpch-kit](https://github.com/gregrahn/tpch-kit) (`make MACHINE=LINUX DATABASE=POSTGRESQL`; macOS uses `MACHINE=MACOS`; Windows via WSL2).
2. `createdb tpch`, `psql tpch -f dss.ddl`.
3. `./dbgen -vf -s 1`, then for each table strip the trailing `|` with `sed 's/|$//'` and load with `\copy <table> FROM ... CSV DELIMITER '|'`.
4. `psql tpch -f tpch_keys.sql`.
5. Verify with `\d lineitem` that decimals are `numeric(15,2)` and that PK/FK constraints exist.

**`db/roles.sql`** creates:

- `nl2sql_ro`: `LOGIN`, `SELECT` on the 8 TPC-H tables only, no other privileges. `ALTER ROLE nl2sql_ro SET statement_timeout = '10s'; ALTER ROLE nl2sql_ro SET default_transaction_read_only = on;`.
- `nl2sql_cache`: read/write on the cache schema only (pgvector table), no access to TPC-H tables.
- The generated SQL is executed only as `nl2sql_ro`. The cache is written only as `nl2sql_cache`.

**`db/connection.py`** provides `run_readonly(sql, params=None, timeout_ms=10000) -> ExecResult(rows, columns, elapsed_ms)` using a pool. It sets `SET LOCAL statement_timeout` and `SET TRANSACTION READ ONLY` per call and rolls back after. Every other module calls the DB only through this function (plus `explain()` in `execution/cost.py`, which wraps it).

**Tests that need a live DB** are marked `@pytest.mark.db` and skipped when `NL2SQL_TEST_DSN` is unset. The guardrail tests must include a live check that a `DROP TABLE`, an `INSERT`, and a `SELECT pg_sleep(30)` are all rejected or timed out under `nl2sql_ro`.

**Open schema decision (ask the human, do not guess).** `orders.o_totalprice` carries the synonym "revenue" in `schema_graph.json`, but TPC-H defines revenue as `sum(l_extendedprice * (1 - l_discount))`. Add a `metric_definitions` entry to the config that maps "revenue" to the lineitem expression, and flag in the README that the synonym on `o_totalprice` should be removed once the human decides. Until then the question set in section 8 must avoid the bare word "revenue".

## 6. Module specs, part 1: input to executed candidates

### 6.1 ASR (`asr/`)

- `Transcriber.transcribe(audio_path_or_bytes) -> Transcript` using faster-whisper (model size from config, default `small`, `compute_type="int8"` on CPU), `word_timestamps=True`.
- `Transcript`: `text`, `segments` (each with `avg_logprob`), `words: list[WordInfo(text, start, end, probability)]`, `language`, `elapsed_ms`.
- faster-whisper gives no n-best list. Do not fake one. Uncertainty comes only from `Word.probability` and `Segment.avg_logprob`.
- Typed fallback: `Transcript.from_text(text)` sets all word probabilities to 1.0 and `source="typed"`. The orchestrator treats it identically afterwards.
- Tests: typed fallback; a fake transcriber; one real short WAV fixture marked `@pytest.mark.slow`.

### 6.2 DB-aware correction (`correction/`)

- `build_vocab(schema, conn)` collects table and column names, synonyms, and distinct values of low-cardinality filterable columns (e.g. `c_mktsegment`, `r_name`, `n_name`, `l_shipmode`, `o_orderpriority`). Cache to disk.
- `correct(transcript, vocab) -> CorrectionResult(text, edits)`; each edit has `original`, `replacement`, `score`, `asr_probability`, `kind` (name / value). Use rapidfuzz `WRatio` over word n-grams (1–3 tokens). Only replace when the original word is **not** already a vocab term **and** score ≥ threshold (config, default 85). Never rewrite numbers or dates.
- Prefer replacing words with low ASR probability; a high-probability word needs a higher score to be replaced.
- Every edit is logged, because edits feed clarification signals (section 7). Tests include homophone-style cases ("Brazil" vs "Brasil", "automobile" vs "automobiles") and a case where nothing should change.

### 6.3 Policy retrieval (`policy/`)

- `registry.json` entries: `{id, kind: "pattern"|"heuristic"|"safety", applies_when: {ir_conditions}, text, source}`. Write at least 20 entries by hand. Every heuristic must cite a source (PostgreSQL docs section or a standard textbook rule) in `source`; do not invent rules.
- Pattern entries: top-N per group, ranking with ties, running totals, year-over-year, anti-join ("customers with no orders"), share of total, having-filter on aggregates, date-range boundaries (half-open intervals).
- Heuristic entries (shape-oriented, not stats-oriented): filter early on selective predicates; project only needed columns; prefer `EXISTS` over `IN (subquery)` for anti/semi joins; avoid correlated subqueries when a pre-aggregated join is equivalent; do not reference a CTE twice if the filter should be pushed in (it is materialized); use `LEFT JOIN ... IS NULL` or `NOT EXISTS`, never `NOT IN` with nullable columns.
- `retrieve(ir, k=5) -> list[Policy]`: first rule-based matching on IR fields (has subquery, has having, has time\_filter, group\_by present, metric aggregate type, ...), then fill remaining slots with sentence-transformers similarity over `text`. Return a stable order.
- Tests: a given IR always selects the same policy ids; every registry entry validates against a Pydantic model.

### 6.4 SQL generation (`generation/`)

- `LLMClient` interface: `complete(system, user, temperature, n) -> list[str]`. Implement `AnthropicClient`, `OpenAIClient`, and `FakeLLM` (returns canned strings, used in all unit tests). Provider and model name from config.
- `generate_candidates(ir_text, policies, linked_schema, n=3) -> list[Candidate(sql, strategy, prompt_hash)]`.
- **Candidates must be shape-diverse.** Use one prompt template with a different `strategy` instruction per candidate: (1) straightforward joins + GROUP BY, (2) pre-aggregate in a subquery or CTE then join, (3) `EXISTS` / semi-join form where the IR allows it. Do not ask for different join orders; the planner decides that. If a strategy does not apply to the IR, generate fewer candidates and record why.
- Prompt content, in this order: role + hard safety rules; the rendered IR (from `ir/render.py`); the exact table/column list with types for the entities involved; join conditions written out (including the composite lineitem→partsupp condition when needed); retrieved policies; 2–3 nearest validated examples (question, IR, SQL) from the execution cache when available; output contract ("return one SELECT statement in a fenced `sql` block, nothing else").
- **Do not include row counts, column statistics or index lists in the prompt.** Parse the fenced block; reject anything else. Log `prompt_hash` and token counts.
- Tests: prompt contains every required section; no statistics leak into the prompt; the parser rejects multiple statements.

### 6.5 Code-level guardrails (`guardrails/`)

- `validate_sql(sql, schema) -> ValidationResult(ok, reasons, normalized_sql)` using `sqlglot` (Postgres dialect). Rules: exactly one statement; top-level `SELECT` or `WITH ... SELECT`; no DDL/DML; no `pg_*` / `information_schema` / system catalog access; no functions outside an allowlist (config); every table and column exists in the schema graph; no `SELECT *` on lineitem without a LIMIT.
- `enforce(sql, max_rows=1000)`: wrap or append a `LIMIT` if the outermost query has none or has a larger one. Keep the original in the trace.
- A failed validation is not a crash. The candidate is dropped and the reason is logged; if all candidates fail, return a typed `PipelineResult(status="refused", reasons=[...])`.
- Tests: a table of at least 25 malicious or malformed inputs (stacked statements, comments hiding keywords, `COPY`, `SELECT INTO`, `pg_sleep`, `;--`, unknown tables, CTE-wrapped DML such as `WITH x AS (DELETE ...)`), all rejected; a table of 25 valid TPC-H-style queries, all accepted.

### 6.6 Execution and equivalence (`execution/runner.py`, `execution/equivalence.py`)

- `execute_all(candidates) -> list[Execution(candidate, rows, columns, elapsed_ms, error)]` through `run_readonly`. Run sequentially (simple, deterministic). An error or timeout marks that candidate failed but does not stop the others.
- `canonicalize(execution, order_matters) -> Hashable`: sort rows unless the IR has `order_by`; round floats/decimals to 2 places; normalize column names to positional order but compare column count and types; treat `None` consistently. If `order_by` + `limit` are present, compare the ordered list.
- `group_by_equivalence(executions) -> list[Group(canonical_key, members)]`. Output order: largest group first, ties by earlier candidate index.
- Tests: same rows in a different order are equal when no ORDER BY; differ when ORDER BY is present; 2.499999 vs 2.50 are equal after rounding; empty results from two candidates are equal only if column counts match.

## 7. Module specs, part 2: selection to dashboard

### 7.1 Cost-based selection (`execution/cost.py`)

- `explain(sql) -> PlanInfo(total_cost, planning_ms, execution_ms, plan_json, plan_fingerprint)` using `EXPLAIN (ANALYZE, FORMAT JSON)` as `nl2sql_ro` under the same timeout. `plan_fingerprint` is a hash of the plan tree's node types and relation names with costs and row counts removed, so identical plan shapes can be counted.
- `select_best(group) -> Selection(candidate, reason, plan_infos)`. Choose the group first: the largest equivalence group wins; if the top two groups are the same size, or the second group has ≥ 40% of candidates, do **not** choose. Return `status="disagreement"` and let clarification handle it.
- Inside the chosen group choose by lowest `total_cost`; tie-break (within 5%) by median `execution_ms` over 3 runs, then by shorter SQL. Record all of it.
- Record whether the plans in the group were identical (`all_plans_identical`). This is the measured answer to "does cost selection ever have anything to choose between", and it goes into the report. Do not hide it if the answer is "rarely".
- Tests: fake plan JSON fixtures for selection logic; one live-DB test that two syntactically different but equivalent queries produce parseable plans.

### 7.2 Clarification (`clarification/`)

Three signal sources, each returning `Signal(source, field, severity 0–1, detail)`:

1. **ASR**: a corrected or entity word with `probability` below a threshold (default 0.6), or a correction with a score within 5 points of the threshold, or segment `avg_logprob` below a threshold.
2. **IR**: any slot whose self-consistency confidence is below the ask threshold (default: ask at 0.33; 0.67 is accepted but logged). Also any `unsupported` flag and any ambiguous time column from `time_resolver`.
3. **Candidates**: result groups disagree (from 7.1).

Rules:

- `needs_clarification(signals) -> ClarificationRequest | None`. At most one question per round, ordered by severity; maximum 2 rounds (config).
- Questions are multiple-choice, built deterministically from data, not by an LLM: for an IR slot, the distinct values seen across the 3 extraction runs (if the extractor does not expose per-run values, add `extract_ir_with_runs` to `nl2sql_ir/ir/extractor.py` without changing `extract_ir`'s behaviour); for an ASR doubt, the heard word vs the top 2 vocab matches; for candidate disagreement, a plain-language description of how the top two result groups differ (different row counts, different top row, different total) plus a preview of 3 rows each.
- Always include "none of these" which falls back to a typed answer.
- `merge_answer(ir_or_text, request, choice) -> updated input`, then the orchestrator re-runs from the earliest affected stage (ASR/correction doubt → re-link and re-extract; IR slot → patch the IR field and set its confidence to 1.0; disagreement → pick the chosen group, no regeneration).
- Log every clarification: signal source, question, choice, whether the final answer matched gold (eval only).
- Tests: each signal source fires on a constructed case; a high-confidence case asks nothing; the round limit holds.

**Optional, behind `config.clarification_memory = False` (build only after sections 8 and 9 are done).** `clarification/memory.py` stores past clarification events and learns (a) speech confusions (heard word → confirmed term), (b) term → schema mappings confirmed by the user, (c) per-signal ask thresholds. Rules: update only from user-confirmed outcomes; keep a validation window (a learned mapping is applied only after it has been confirmed twice); apply decay so old entries fade; thresholds move in small bounded steps and never below a floor set in config. Evaluate with a simulated user over a sequence of queries, including a mid-stream preference/profile switch, and report clarification rate and accuracy per block of queries. Do not claim this is novel in the code or README; describe it as "learned clarification policy".

### 7.3 Caches (`cache/`)

- **Execution cache** in Postgres with pgvector: table `exec_cache(id, question, embedding vector(384), ir_json, sql, validated bool, row_count, columns_json, hit_count, created_at)` created by a migration script. Embeddings from the same all-MiniLM-L6-v2 model as stage 1.
- `lookup(question, ir) -> CacheHit | None`: exact hit when cosine ≥ 0.95 **and** the IR hash matches; the cached SQL is re-executed (cache SQL, not rows). Near neighbours (0.75–0.95) are not hits; they are returned by `neighbours(question, k=3)` for use as few-shot examples in 6.4.
- `add(question, ir, sql, validated)`: only when validated. Validated means the user confirmed it, or all candidates were in one result group and no clarification was needed. Also append `(question, IR)` to the stage-1 `FewShotStore` at the same moment.
- Evaluation protocol: cold run, then warm run on the same set, plus a paraphrase set. Report hit rate and latency separately for exact hits and misses.
- Tests: no add without validation; threshold boundaries; a paraphrase does not exact-hit when the IR differs.

### 7.4 Dashboard (`dashboard/`)

- `chart_mapper.map_result(columns, rows, ir) -> ChartSpec` is **deterministic**, no LLM. Rules in priority order:
  1. One row, one numeric column → KPI card.
  2. One date/time column + one numeric → line. Date + categorical + numeric → one line per category (cap 6, rest grouped as "other").
  3. One categorical + one numeric → bar; horizontal and top-N (default 10) when more than 8 categories.
  4. Two numeric columns → scatter.
  5. Anything else, or more than 3 dimensions → table only.
- Column roles come from schema tags (time / metric / entity\_name / filterable) and dtype, not from guessing names. A table view is always available beside the chart. No pie charts.
- `app.py` (Streamlit + Plotly): input panel (mic via Streamlit audio input, plus a text box); then panels for transcript with corrections highlighted, IR (collapsible JSON), generated SQL candidates with equivalence groups, selected plan cost, clarification prompt (radio buttons), the chart + table, and a per-stage latency bar. A sidebar toggle switches between baseline and full pipeline so the demo can show the difference live.
- Tests: one test per rule in `chart_mapper`; the app imports cleanly (no UI tests required).

### 7.5 Orchestrator and trace (`pipeline/`)

- `run_pipeline(input, session) -> PipelineResult` where `PipelineResult` has `status` in {`ok`, `needs_clarification`, `refused`, `unsupported`, `error`}, plus transcript, corrections, linked schema, IR, policies, candidates, groups, selection, final SQL, columns, rows, chart spec, clarification request, and a `Trace`.
- `resume(session, choice)` continues after a clarification (state kept in `session`, a plain dataclass; no server state).
- Control flow lives in one readable function with explicit `if` branches per status. No hidden retries beyond: one regeneration if every candidate fails validation or errors, with the error messages added to the prompt.
- `Trace` records per stage: start/end time, inputs hash, outputs summary, signals raised, and token counts and model name for LLM calls. Written as JSONL, one line per question. The eval harness reads only this file.

## 8. Evaluation harness

Every number in the final report must come from `eval/` scripts reading the trace JSONL. Fix the index set, the model names, the seeds and the question file before the first run, and record them in each output.

**Question set (`eval/questions.json`).** 100–150 questions, each with `id`, `text`, `type_tags`, `gold_sql`, `answerable` (bool), `spoken` (bool), and for spoken ones an audio path. Cover: simple aggregates, group-by, top-N, multi-table joins (including the composite lineitem→partsupp path), time-relative filters, having, nested/subquery, anti-join, share-of-total. Include 10–15 **ambiguous** questions (e.g. an unqualified "revenue", an unclear date column) and 8–10 **unanswerable** ones (asking for a column that does not exist). Do not use the bare word "revenue" until the human settles the synonym question in section 5. At least 40 questions are also recorded as audio (the human records them; you provide the script and a naming convention). Gold SQL is hand-written and checked by running it.

- [ ] **Metrics.**

| Metric | Definition |
| --- | --- |
| Execution accuracy | Result of final SQL equals result of gold SQL under the same canonicalization as 6.6. |
| Abstention quality | On unanswerable and ambiguous questions: fraction that end in `unsupported`, `refused`, or a clarification instead of a confident wrong answer. |
| Confident-wrong rate | Final status `ok`, no clarification, result not equal to gold. This is the headline error number. |
| Clarification rate and precision | How often it asked; of those, how often asking was warranted (the first-pass answer would have been wrong). |
| Latency | Per stage and total, median and p95, warm vs cold. |
| Cache | Hit rate and latency for exact hits, near-neighbour use, misses. |
| Cost selection | Fraction of questions with more than one equivalent candidate; fraction of those with identical plans; cost ratio best/worst. |

**Scripts.**

1. `run_baseline.py`: naive path. Transcript text goes straight to one LLM call with the raw schema DDL, one SQL, executed. No correction, no IR, no policies, no candidates, no clarification. Use the same LLM and the same guardrails as the full pipeline, so the comparison isolates the pipeline's own stages. Typed and spoken runs reported separately.
2. `run_full.py`: the full pipeline, with a simulated user for clarification: the harness answers a multiple-choice question by choosing the option that matches the gold answer, or "none of these" if no option does. Report accuracy both with and without clarification enabled.
3. `join_order_invariance.py`: for 15–20 multi-join TPC-H-style gold queries, generate every permutation (cap at 24 per query) of the `FROM`/`JOIN` order among **inner** joins, run `EXPLAIN` with default settings, and count how many produce an identical `plan_fingerprint`. Also repeat with `join_collapse_limit = 1` to show the effect when the planner is not free to reorder. Report the result as measured; the expectation (mostly identical plans under defaults) is a hypothesis, not a claim.
4. `shape_cost_compare.py`: for 15–20 questions where equivalent formulations exist (join vs `EXISTS`; pre-aggregated subquery vs direct join; CTE referenced once vs twice), verify equivalent results, then compare plan cost and execution time. This is the evidence for or against shape-aware generation.
5. `report.py`: emits Markdown tables and PNG charts into `eval/out/`, including the per-stage latency breakdown and a failure listing (question id, stage where it went wrong, signals that fired). Failure listings are required; do not only report aggregates.

**Ablations** (flags in `config`, each run on the same question set): without correction; without policies; single candidate instead of 3; without cost selection; without execution cache; without clarification. One table with accuracy, confident-wrong rate, clarification rate and median latency per row.

## 9. Working order, definition of done, limits

**Working order.** Each step ends with its tests passing and a three-line status. The text-only vertical slice comes early so that something runs end to end before the extras are built.

1. Read `nl2sql_ir/`; run its tests; write `pipeline/adapters.py` if needed.
2. `config.py`, `db/` (roles, connection, setup doc). Live checks if a DB is available.
3. `schema/join_paths.py` (BFS, composite FK) and `ir/render.py`.
4. `guardrails/` with the full malicious/valid test tables.
5. `execution/runner.py` and `equivalence.py`.
6. `policy/` registry and retriever.
7. `generation/` with `FakeLLM` tests first, then the real clients.
8. `execution/cost.py`.
9. **Vertical slice:** `pipeline/orchestrator.py` for typed input only, no clarification, no cache. Run 10 questions end to end and print the trace.
10. `clarification/` signals, questions, merge, and wire into the orchestrator.
11. `cache/execution_cache.py` and its wiring (including few-shot neighbours).
12. `correction/` and `asr/`, wire in the voice path.
13. `dashboard/` (`chart_mapper` first, then Streamlit).
14. `eval/`: question file skeleton, baseline, full, invariance, shape-cost, report.
15. Only if everything above passes: `clarification/memory.py` and its simulated-user evaluation.

**Definition of done.**

- `pytest` passes without a DB, API key or microphone (fakes for all three); `pytest -m db` passes against a loaded TPC-H database.
- `python -m nl2sql_app.eval.run_full` and `run_baseline` produce the report tables from a fixed question file with no manual steps.
- `streamlit run nl2sql_app/dashboard/app.py` shows the full path for a typed and a spoken question, including a clarification round, and the baseline toggle.
- The README lists the exact commands to reproduce every table, the pinned model names, the index set, and the known limitations.
- Every guardrail in 6.5 has a test that fails if the guardrail is removed.
- The report includes the confident-wrong rate, the failure listing, the identical-plan fraction, and the ablation table, whatever the numbers turn out to be.

**Do not.**

- Add LangChain, LlamaIndex or an agent framework.
- Pass row statistics, row counts or index lists to the LLM, or describe the LLM as optimizing join order.
- Let cost selection choose between candidates whose results differ.
- Run generated SQL as any role other than `nl2sql_ro`, or skip the validator when a cache hit supplies the SQL (cached SQL is re-validated and re-executed).
- Add to either cache before validation.
- Change `schema_graph.json`, `tpch_keys.sql`, or the index list without asking.
- Invent citations or claim novelty in code, comments or the README. Describe what the system does and what was measured.
- Edit files in `nl2sql_ir/` except for a proven bug or the two additions named in section 3 and 7.2.

**When blocked.** Stop, state the blocker and the smallest decision that would unblock it, and continue with any step that does not depend on it.
