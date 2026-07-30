---
name: sigma-byod-data-model
description: >-
  BUILDING BLOCK — the BRING-YOUR-OWN-DATA front door. Profile a CLIENT'S OWN
  warehouse table (types, cardinality, null rates, date range, candidate measures
  vs dimensions vs grain key), propose a shaping, then emit and PUBLISH a real
  Sigma DATA MODEL as code via `POST /v2/dataModels/spec`, which workbooks then
  source with `source:{kind:"data-model",dataModelId,elementId}`. For a full
  branded company dashboard / POV / demo, use **sigma-company-dashboard** — it
  calls THIS skill first whenever the client brings their own connection and
  table instead of the sample data. If there is NO table at all — nothing to
  point at, or a schema but no data — use **sigma-synthetic-star-model**, which
  fabricates a star schema from a DDL or schema file. Use this DIRECTLY when the ask is "use OUR
  data", "point it at my own Snowflake / Databricks table", "point it at
  <DB>.<SCHEMA>.<TABLE>", "profile this dataset", "what's in this table", "build a
  data model / semantic layer from this table", or "bring your own data". Encodes
  the profile → propose → publish → VERIFY loop, the read-connection vs
  WRITEBACK-connection split (input tables need write access; reads do not), and
  the dialect-free warehouse-table + Sigma-formula path — verified end-to-end on
  BOTH Snowflake and Databricks. Defers field-level data-model JSON to the
  upstream **sigma-data-models** skill; this owns the workflow and the decisions.
---

# Sigma BYOD data model — from a client's table to a published semantic layer

Given "use our own data," profile the client's table, agree a shaping with them,
and publish a real Sigma data model that every downstream workbook sources from.
Every shape below was verified live against staging on both a Snowflake and a
Databricks connection — not taken from docs.

**The one idea:** the BYOD path emits **no warehouse SQL at all**. The source is
`warehouse-table`, and all shaping is Sigma formulas, which Sigma compiles to
whichever dialect the connection speaks. That is why the identical spec works on
Snowflake and Databricks with zero changes.

```
client table ──(warehouse-table)──> DATA MODEL ──(data-model source)──> WORKBOOK
             dialect-free           Sigma-formula shaping              any generator
```

## The four moves

1. **Pick connections** — `scripts/api/list-connections.sh`, then ASK the human.
   A read connection and a writeback connection are two different questions.
2. **Profile** — `scripts/profile-table.py <conn> <DB> <SCHEMA> <TABLE> --out profile.json`.
   Returns stats plus `candidates` per role. `--folder` is optional: it defaults to
   your own home folder for the scratch workbook. Pass it to put that workbook
   somewhere specific — e.g. beside the deliverable.
3. **Propose** — show the human the candidate roles and confirm before generating.
   Then `examples/build_byod_data_model.py profile.json --name … --folder … --out spec.json`.
4. **Publish + verify** — `scripts/api/publish-datamodel.sh post spec.json`. It
   validates first, publishes, then auto-verifies. Do not skip the verify.

## Connection selection — read vs writeback are different questions

`scripts/api/list-connections.sh` returns `{connectionId, name, type, writeAccess,
writebacks}`. Use `--writable` to list only write-enabled ones.

- `type` gives the dialect for free. Verified values: `snowflake`, `databricks`,
  `postgres`, `mysql`, `bigQuery`, `sqlserver`, `clickHouse`, `azuresql`,
  `alloydb`, `starburst`, `emulator`.
- Ask **"which connection holds the data to analyze?"** → the READ connection.
- Ask **"which connection should own input tables?"** only if the build includes a
  scenario/planning/cohort page → the WRITE connection. Default it to the read one.

**⚠ GOTCHA — input tables require a WRITEBACK-ENABLED connection; reads do not.**
`source:{kind:"empty",connectionId}` and linked input tables only work when the
connection has `writeAccess: true` and a `writebacks` destination. Reads work on
any connection, so a read-only connection produces a dashboard whose charts render
and whose input tables silently do nothing. In one real org only **16 of 52**
connections were write-enabled — assume the client's is not until you check.

Write access is an **Admin-only** toggle (Administration → Connections → Edit →
Enable write access → set a write destination). Nothing in this repo can turn it
on. If it's off, say so plainly and name the toggle; don't work around it.

## Profiling — what it measures, what it can't

`scripts/profile-table.py` has three modes:

- `--via workbook` (best, and what `auto` does) — creates a **scratch workbook**
  running one aggregate query, reads it back, and reports `probeWorkbookId`,
  `probeFolderId` + a cleanup hint. It does **not** self-delete; deletion stays on
  the direct-curl path. Say up front that profiling creates a file in their org,
  and delete it when you're done.
  The destination folder defaults to the caller's home folder
  (`/v2/whoami` → `/v2/members/{id}.homeFolderId`); `--folder` overrides it. If an
  explicit `--via workbook` can't resolve a folder it **errors** rather than
  quietly downgrading to statistics-free output.
- `--via columns-only` — names, types and warehouse comments only. No SQL, no
  object created. This is where a locked-down connection lands; every stat is
  `null` and roles fall back to type inference. Handle it gracefully.
- `--via auto` (default) — tries `workbook`, degrades to `columns-only`.

It emits `candidates` for `dateColumn` / `grainKey` / `measures` / `dimensions` /
`excluded`, plus `warnings`. Scoring thresholds are in
`reference/profiling-heuristics.md`.

**⚠ GOTCHA — identifier quoting is dialect-specific.** Databricks reads
`"PRICE"` as a *string literal*, not a column, so `COUNT("PRICE")` silently
counts a constant. The profiler emits backticks for `databricks` and double
quotes elsewhere. Any SQL you hand-write against a client connection needs the
same care.

**⚠ GOTCHA — a stale table breaks period comparison silently.** If
`max(date)` is long past, a `Today()`-anchored "Current Period" is simply EMPTY
and every comparative KPI reads zero. The profiler raises `stale-max-date`; the
generator defaults to anchoring on the table's max date instead.

## The shaping proposal — what belongs in the model

Confirm roles with the human, then put the shaping in the model, not in SQL:

- **Passthrough columns** — `[<ElementName>/<Friendly Name>]`, where ElementName is
  the element's `name` (keep it equal to the table name) and Friendly Name is
  Sigma's derived display name: `PRODUCT_TYPE` → `Product Type`, `store_key` →
  `Store Key`.
- **Computed columns** reference siblings **bare**: `[Price] - [Cost]`,
  `DateTrunc("month", [Date])`, `If([Date] > DateAdd("year",-1,Date("2025-02-19")),
  "Current Period", "Prior Year")`. Verified working on a `warehouse-table` source.
- **Metrics** also use bare refs and may reference computed columns:
  `Sum([Unit Margin] * [Quantity])`.
- **Period tagging** replaces the sample-data `PERIOD_NAME` SQL trick. Anchor to
  the profiled max date by default; `Today()` only on genuinely live data.

**⚠ GOTCHA — `format` is rejected.** A `format` key on a column or metric fails
with the masked `Missing "kind" field`. Set number formatting on the consuming
workbook element or in the UI.

**⚠ GOTCHA — `timeline.comparison.direction` is rejected** (`Invalid value:
string`). `metrics[].timeline` accepts `{dateColumnId, truncation, comparison:
{comparisonPeriod}}` and round-trips exactly. Use only `comparisonPeriod`.

## Publish + verify — HTTP 200 is not evidence

`POST /v2/dataModels/spec` returns JSON `{"success":true,"dataModelId":"..."}`.
(Note: **not** YAML — the workbook endpoint's response is JSON now too, whatever
older notes say.) Submitted element and column **ids are preserved**, not remapped,
so you can reference them immediately.

But the endpoint's validation is shallow, and this is the trap:

**⚠ GOTCHA — a formula referencing a column that does not exist is ACCEPTED with
HTTP 200.** It becomes a column of type `error`, visible only via
`scripts/api/mcp-describe.sh datamodel-element <id> <elementId>`. Two columns
sharing a `name` are also accepted, and Sigma silently renames the second to
`Name (1)`, so every `[Name]` formula binds to the first.

Both are caught before publishing by `scripts/validate-datamodel-spec.py`, which
`publish-datamodel.sh post|put` runs automatically and which aborts on failure.
After publishing, the same script auto-runs `verify` and fails loudly on any
`error` column. `publish-datamodel.sh verify <dataModelId> <elementId>` runs it
on demand.

**⚠ GOTCHA — column ids are arbitrary.** Existing UI-built models show ids like
`inode-5xe4R8F98RmULVctRm0E37/PRICE`, which looks like a required identifier. It
isn't — that prefix matches neither the table's `inodeId` nor its URL slug. Plain
ids (`c-price`) work fine. Don't waste a call resolving inodes for this.

## Handing the model to a workbook

The workbook's base element sources the model:

```json
{ "kind": "table", "source": { "kind": "data-model",
    "dataModelId": "<from the POST response>", "elementId": "<your element id>" },
  "columns": [ { "id": "w-period",
                 "formula": "[<ElementName>/Period Name]", "name": "Period Name" } ] }
```

The formula prefix is still the **data-model element's name**. Downstream chart
and KPI formulas then reference the workbook table's own column display names, as
usual. Prefer the model's `[Metrics/<Name>]` over re-deriving sums in the workbook.

Then **open it in a browser.** Cross-element column references fail at render
time, not at POST time, and no headless session can see that. End every build by
handing over the URL and asking for confirmation.

## Files

- `examples/build_byod_data_model.py` — the canonical generator: profile JSON +
  role flags → a publishable data-model spec, with passthrough columns, a `Month`
  truncation, a max-date-anchored `Period Name`, `Sum`/`CountDistinct` metrics and
  one timeline comparison. Swap the role flags for your table; the mechanic
  doesn't change.
- `examples/profile-example.json` — real `profile-table.py` output (trimmed), so
  you know the shape without running it.
- `reference/profiling-heuristics.md` — the role-scoring thresholds, the friendly-
  name rule, and the unusable-column-name cases.
