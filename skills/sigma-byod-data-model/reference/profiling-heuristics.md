# Profiling heuristics — how a column gets a role

What `scripts/profile-table.py` uses to turn measurements into `candidates`.
These are **proposals for a human to confirm**, never decisions. On a client's
data the names are unfamiliar and the wrong guess is expensive, so always show
the candidates and ask.

## Role scoring

Applied in order; first match wins.

| Role | Test | Notes |
|---|---|---|
| `date` | `warehouseType` in `date`, `datetime`, `timestamp`, `timestamp_ntz`, `timestamp_tz` | Ranked by distinct count — the widest-ranging date column is usually the event date, not a `CREATED_AT` audit stamp. |
| `identifier` | numeric **and** name ends in `_ID`, `_KEY`, `_NUMBER`, `_NUM`, `_CODE`, `_UUID`, `_GUID` (or is exactly `ID`/`KEY`) | The name test is load-bearing: without it `ORDER_NUMBER` and `STORE_KEY` look like "numeric, high cardinality" and get proposed as things to `Sum()`. |
| `measure` | numeric and not identifier-shaped | Still imperfect — latitude/longitude and population columns pass this test. Ask. |
| `dimension` | `distinct <= --max-dim-card` (default 50) | Sorted ascending by distinct count, so the most filterable columns come first. |
| `high-cardinality` | text above the dimension threshold | Reported under `excluded` with a reason. Usable as a detail column, bad as a filter. |
| `unknown` | non-text with no stats | Only reachable in `--via columns-only`. |

**Grain key** is separate from role: any column with `distinct / rowCount > 0.95`
is a grain candidate. If none qualifies, the `identifier`-role columns are offered
instead. A near-unique column means one row per entity; falling back to name shape
matters because a fact table's `ORDER_NUMBER` repeats across line items (in a real
10M-row table it scored 0.31, and the warehouse comment said so explicitly).

## Degraded mode

With `--via columns-only` there are no stats. Every numeric column that isn't
identifier-shaped becomes a `measure` candidate, every text column becomes a
`dimension` candidate, and `distinct` / `nullRate` / `min` / `max` are all `null`.
Say so when presenting candidates — the user is choosing with less information
than usual.

## Friendly names

Sigma derives a display name from the warehouse column name, and that derived
name is what goes on the right of the slash in a passthrough formula
`[<ElementName>/<Friendly Name>]`. Getting it wrong is the most common way a
BYOD model fails.

The rule (verified): split on `_` **and at every letter↔digit boundary**, then
capitalize each resulting piece.

| Warehouse | Friendly | Formula |
|---|---|---|
| `PRODUCT_TYPE` | `Product Type` | `[big_buys_pos/Product Type]` |
| `SKU_NUMBER` | `Sku Number` | `[big_buys_pos/Sku Number]` |
| `store_key` | `Store Key` | `[big_buys_pos/Store Key]` |
| `CUST_KEY` | `Cust Key` | `[big_buys_pos/Cust Key]` |
| `primary_diagnosis_icd10` | `Primary Diagnosis Icd 10` | `[encounters/Primary Diagnosis Icd 10]` |
| `readmission_within_30d` | `Readmission Within 30 D` | `[encounters/Readmission Within 30 D]` |

Note `SKU` → `Sku` and `CUST` → `Cust`: it is a plain capitalize, not an
acronym-aware transform. Don't "fix" it to `SKU`.

**⚠ The letter↔digit split is easy to get wrong and this repo got it wrong until
2026-07-30** — the old rule left a mixed alphanumeric token alone (`icd10`,
`30d`), which produced a name that does not resolve, and the data-model POST
failed with `dependency not found: formula reference 'encounters/…'`. Any column
mixing letters and digits in one token is affected: `icd10`, `30d`, `q1`, `y2024`,
`top10`.

**Fastest way to settle a name empirically:** `POST /v2/dataModels/spec` reports
**every** unresolved reference in a single 400 response. So put a dozen candidate
spellings in one throwaway spec as separate columns and read off which ones it
does *not* complain about — those are the real names. Nothing is created when the
POST fails, so this costs one call and leaves no artifact. The lookup is
case-insensitive and treats `_` as a space, so candidates differing only in case
or underscores collapse into one reported error.

The **left** side of the slash is the element's `name`, which is whatever you set
it to — the generator defaults it to the table name (`big_buys_pos`), so the
prefix is the raw table name including its original case.

## Column names that cause trouble

| Case | Effect | Handling |
|---|---|---|
| contains `/` | The slash is the source/column separator, so `[T/A/B]` is ambiguous | Profiler warns `unusable-column-name`. Give the column an explicit `name` in the model and keep the passthrough formula pointing at the real one. |
| contains `-` | Parsed as subtraction in some positions | Same. |
| duplicate names after normalization | Sigma silently renames the second to `Name (1)`; every `[Name]` binds to the first | `validate-datamodel-spec.py` fails on this before publish. |
| hidden columns (`visibility != "included"`) | Not queryable, but still listed by the columns endpoint | Profiler filters them out. |

## Warnings the profiler raises

| Code | Meaning |
|---|---|
| `stale-max-date` | `max(date)` is more than 400 days old. A `Today()`-anchored period tag yields an EMPTY current period. Anchor to the max date instead. |
| `no-date-column` | No date/datetime column — no trend chart, no period comparison. |
| `no-measures` | No non-identifier numeric column — KPIs can only be counts. |
| `no-write-access` | The connection cannot host input tables. Reads are unaffected. Admin-only fix. |
| `unusable-column-name` | See the table above. |
| `stats-unavailable` | Fell back to `columns-only`; the detail carries the underlying error. |
