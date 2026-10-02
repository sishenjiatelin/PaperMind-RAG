# Service Operations Study Reference

Read this for D11–D14. Paths below are relative to the repository root. Use the current code and tests for implementation claims; milestone docs explain intent and historical results.

## Evidence routing

| Domain | Read first | Follow the selected sub-topic into |
|--------|------------|------------------------------------|
| D11 / M0–M1 | `service_operations/docs/M0_FOUNDATION.md`, `service_operations/docs/M1_IMPLEMENTATION.md` | `generate_m0.py`, `config/m0.json`, `warehouse/`, `metrics/query.py`, `tests/test_m1.py` under `service_operations/` |
| D12 / M2 | `service_operations/docs/M2_IMPLEMENTATION.md` | `manuals/registry.py`, `manuals/search.py`, `manuals/cli.py`, `docs/manual_sources.json`, `tests/test_m2.py` under `service_operations/`; collection-specific ingestion code under `src/` |
| D13 / M3 | `service_operations/docs/M3_IMPLEMENTATION.md` | `service_operations/contracts.py`, `service_operations/query_service.py`, `service_operations/cli.py`, `service_operations/tests/test_m3.py` |
| D14 / M4–M5 | The relevant `service_operations/docs/M4_IMPLEMENTATION.md` or `service_operations/docs/M5_EVALUATION.md` | `service_operations/dashboard.py`, `service_operations/dashboard_data.py`, `service_operations/evaluation/`, `service_operations/evaluate.py`, `service_operations/tests/test_m4.py`, `service_operations/tests/test_m5.py` |

Consult `service_operations/DEV_SPEC.md` for acceptance criteria and `service_operations/docs/ARCHITECTURE.md` for the full data flow. The extension reuses selected core components; its manual index does not run through the original ingestion pipeline. The original Dashboard registers its page in `src/observability/dashboard/app.py`.

## Implementation boundaries for questions and scoring

### D11: data, snapshots and metric definitions

- Assets, sites, work orders and SLA policies are synthetic. The fixed seed and file hashes support reproducibility; injected trends describe this sample only. `NOZZLE_WIPE` and the other fault categories are project classifications, not manufacturer error codes.
- Import validates the complete batch. A rejected batch records quality issues but leaves the active snapshot unchanged. A successful snapshot's business rows and active pointer are committed together; execution failure rolls them back. Input hashes plus schema version support no-op reimports; old snapshots remain queryable.
- Dates use `Asia/Shanghai`, converted to UTC with `[start,end)` boundaries. MTTR assigns resolved work orders by resolution time. SLA assigns by due time, counts only orders due by `as_of`, and includes overdue open orders as failures. Policies are selected at work-order opening time; completion exactly at the deadline succeeds.
- Repeats assign new work orders by opening time. A predecessor must be resolved, match `asset_id + fault_code`, and resolve in `[opened_at-720h,opened_at)`. It can lie before the query period. Count a new order once even if it has several predecessors.
- Zero denominator yields `null` and `NO_ELIGIBLE_RECORDS` at the metric layer; the query service exposes its own warning codes. Results carry numerator, denominator, eligible IDs, filters, metric version and snapshot. Inspect both layers when explaining warnings.

Useful angles: why batch atomicity matters, why filtering history to the current period breaks repeat rates, and how equal-to-boundary timestamps affect SLA and repeat tests.

### D12: source identity, versions and retrieval

- Separate `logical_doc_id`, project `version`, physical `source_hash`, and business effective interval. `demo-registry-v1` is a project registry identifier; the configured adoption date is not the vendor publication date. Official PDFs and the project's two service-record notes have different provenance and purpose.
- Each physical version has an isolated Chroma collection and BM25 file. Select published versions by model, fault category and effective time **before** retrieving; a more similar expired version must not enter the candidates.
- Index publication follows `staged → published` after both indexes succeed. Failure uses `error` with cleanup/retry; deletion uses `deleting` before removing indexes and registry records. Ask about injected failures and whether another version remains intact.
- `HashEmbedder` produces deterministic 384-dimensional lexical hash vectors. It is a local baseline, not a trained semantic embedding model. The other branch uses BM25, followed by RRF and lexical ranking. Read the actual ranking code before explaining additional boosts.
- Page-local chunks retain physical PDF page numbers, which may differ from printed numbers. The core ingestion history now deduplicates by content hash **and collection**, migrates the old schema, and supports collection-specific removal; study D10 alongside D12.5 when useful.

Useful angles: why version filtering after Top-K can lose valid evidence, how failed dual-index publication is hidden from queries, and how same-PDF/different-collection ingestion differs from duplicate ingestion.

### D13: routing, comparisons and partial evidence

- `ServiceOpsRequest` validates fields, enums, periods and timezone-aware instants. `auto` uses rules for three predefined metrics and document cues. Dates and filters are structured inputs; the service does not generate arbitrary SQL from the question.
- Comparisons use adjacent equal-duration intervals with the same snapshot and `as_of`. Ratio differences are percentage points; MTTR differences are hours. Derive the previous period from the code rather than assuming calendar quarters.
- `as_of` controls SLA eligibility and manual applicability. `snapshot_id` selects a dataset. A historical `as_of` does not reconstruct what records were visible in the database on that historical day. `PERIOD_AFTER_AS_OF` rejects metric periods extending beyond the query instant.
- `NOZZLE_WIPE` maps to `PROBE FAIL CLEAN NOZZLE` in manual queries. Technical repair questions use official technical evidence; record-process questions may select the project's applicable note. Record notes cannot substitute for repair instructions.
- Answers format SQL results and cited excerpts using deterministic templates, without a generative model. Missing conditions or ambiguous intent produce warnings. In combined mode, failure on one side preserves valid evidence from the other; a previous-period failure can preserve the current value.

Useful angles: explicit versus automatic mode, unknown or conflicting metric intent, `as_of` versus snapshot history, and which response fields survive a missing database/index.

### D14: UI, evaluation and project presentation

- The Streamlit page calls the shared service; its data helper reads snapshot state, calculates trends through the metric layer, and traces eligible work-order IDs with bounded pagination. UI maintenance actions invoke import/index services. Inspect `tests/test_m4.py` for covered flows rather than claiming browser acceptance from unit tests.
- M5 freezes 100 tasks: 60 metric, 20 document, 12 combined, 8 refusal, with 80 development and 20 holdout cases. Metric expectations come from independent CSV computations, not the SQL implementation under test. Reports include task/data hashes, snapshot, sources, environment and per-case failures.
- Compare raw hybrid and BM25 Top-3 retrieval under the same version gate. Business responses can search more candidates and choose evidence by task type; response citation correctness and raw Hit@3 measure different things.
- The dated M5 report records automatic 100/100 and holdout 20/20, but raw document Hit@3 is 16/20 for both methods; hybrid MRR@3 is 0.8000 versus BM25 0.3667. Attribute these to that fixed run, not current results or production accuracy. If asked for current results, inspect artifacts and rerun only within the requested scope and available inputs.
- Independent human review of PDF semantics, pages and wording remains pending. Synthetic data, generated task templates, lexical embeddings, template answers, and local sequential latency limit the claims made in the case studies. A changed task/data/index or tuned ranking requires a new frozen evaluation and untouched holdout.

Useful angles: why a perfect business-response score can coexist with retrieval misses, how an independent reference avoids circular verification, and what evidence would support a production claim.

## Hands-on guidance

Learning can start by reading code, docs and checked-in samples. Do not initialize the runtime or download manuals as a prerequisite to asking questions. Recommend commands for the selected topic; execute them when requested. All commands below run from the repository root, using the project's existing Python environment.

Quick practice without manual downloads:

```bash
.venv/bin/python -m service_operations.cli --help
.venv/bin/pytest -q service_operations/tests/test_m1.py
.venv/bin/pytest -q service_operations/tests/test_m2.py
.venv/bin/pytest -q service_operations/tests/test_m3.py
.venv/bin/pytest -q service_operations/tests/test_m4.py service_operations/tests/test_m5.py
```

Choose one relevant test file instead of assigning the full sequence. These tests use isolated fixtures; see their source for setup.

For an actual end-to-end demo, follow `service_operations/README.md`: generate the sample, import a snapshot, fetch PDFs, then build the index before combined queries or evaluation. Official PDFs require roughly 49 MB of network downloads and fixed hash verification. Generated databases, indexes and evaluation outputs belong under `service_operations/runtime/` or explicitly isolated paths. Sample/task generators write checked-in example locations by default, so inspect changes before rerunning them.

With runtime inputs already prepared:

```bash
.venv/bin/python -m service_operations.cli status
.venv/bin/python -m service_operations.manuals.cli list
.venv/bin/python -m service_operations.evaluate --split development
.venv/bin/python -m service_operations.evaluate --split holdout
.venv/bin/python scripts/start_dashboard.py
```

The CLI global `--db` precedes the subcommand; `ask --index-dir` follows `ask`. Use the module docs for exact query examples and available options. Treat holdout evaluation as final verification after development choices are fixed.
