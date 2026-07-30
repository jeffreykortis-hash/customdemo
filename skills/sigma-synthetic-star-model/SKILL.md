---
name: sigma-synthetic-star-model
description: >-
  BUILDING BLOCK — the NO-DATA front door. Fabricate a realistic domain dataset
  from nothing — one SQL statement per table, a FACT plus its DIMENSIONS — then
  publish it as ONE Sigma data model with MULTIPLE elements wired by real
  `relationships`: a STAR SCHEMA that workbooks source with
  `source:{kind:"data-model",dataModelId,elementId}`. Input is a pasted
  `CREATE TABLE` DDL or a structured schema file. SIBLING REDIRECTS, check these
  first: if the client HAS a warehouse table, use **sigma-byod-data-model**, not
  this. If the build should reshape the existing Big Buys SAMPLE table into a
  domain, stay in **sigma-company-dashboard** move 1, not this. For a full
  branded dashboard / POV / demo, use **sigma-company-dashboard** — it calls THIS
  skill first whenever the prospect has nothing to point at. Use this DIRECTLY
  when the ask is "make up the data", "generate synthetic <industry> data",
  "we don't have a table yet", "there's no sample data on this connection",
  "here's our schema, mock it up", "build a star schema", or "a fact table and
  dimension tables". Encodes the generate → validate → publish → PROVE-THE-JOIN
  loop, the referential-integrity traps that silently MULTIPLY every measure
  (duplicate dimension keys, orphan FKs, mismatched key types — none of which
  error, they just produce wrong numbers on a beautiful chart), the two-primitive
  dialect surface that makes one spec work on Snowflake AND Databricks, and the
  non-negotiable SYNTHETIC labelling. Defers field-level data-model JSON to the
  upstream **sigma-data-models** skill; this owns the workflow and the decisions.
---

# Sigma synthetic star model — from nothing to a published star schema

Given "we don't have the data," fabricate it. One SQL statement per table
computes rows at query time; **nothing is written to the warehouse**, so this
works on a read-only connection and needs no source table.

```
DDL / schema file ──> schema spec ──> N SQL statements ──> ONE data model ──> workbook
   (user input)       (reviewable)      (per table)        fact + dims       sources
                                                          + relationships     the FACT
```

Verified end-to-end 2026-07-30 on Snowflake and Databricks: `source:{kind:"sql"}`
on a data-model element, multiple elements in one POST, `relationships` round-
tripping intact, and a cross-element JOIN returning correct rows.

## Routing — three ways to source a dashboard

| Does a real table exist? | Use |
|---|---|
| No — nothing to point at | **this skill** |
| Yes, the client's own | `sigma-byod-data-model` |
| Yes, ours (Big Buys sample) | `sigma-company-dashboard` move 1 |

The discriminating question is **"is there a real table we're allowed to point
at?"** This is also the only path that works when an org has a connection but no
usable table at all — generating rows needs no source.

## The four moves

1. **Design the grain — ask.** Fact grain, which dimensions, row count, date
   window. DDL describes shape, never behaviour; `ddl-to-spec.py` returns every
   unanswerable question in `needsInput[]` rather than guessing.
2. **Spec** — `scripts/ddl-to-spec.py <file> --rows N --out spec.json`. Review
   and hand-edit it; it's designed to be read.
3. **Generate + publish** —
   ```
   scripts/gen-synth-sql.py spec.json --dialect snowflake --out ./sql
   examples/build_synthetic_star_model.py spec.json --connection <id> \
       --folder <id> --sql-dir ./sql --out datamodel-spec.json
   scripts/api/publish-datamodel.sh post datamodel-spec.json
   ```
4. **Prove the joins** — `publish` auto-runs `verify-star`. Read its numbers.

## Referential integrity is the whole game

**⚠ A dimension with duplicate primary keys FANS OUT the fact and multiplies
every measure.** This is the one failure that doesn't announce itself. Verified
by deliberately publishing one: the static validator passed, every element
verified clean, the POST returned `success: true` — and only the join numbers
caught it (`dim=9 keys=8 joined=5625` against `fact=5000`, i.e. **12.5% inflated
revenue** on a model that looked perfect everywhere else).

`verify-star` asserts four numbers per relationship:

| | |
|---|---|
| `joined > 0` | the join actually joins — otherwise every FK is an orphan |
| `dim_rows == dim_keys` | **no duplicate dimension keys** |
| `joined == fact_rows` | no orphan FKs, no fan-out |
| key types in one family | a mismatched-type join **502s the server** rather than erroring |

FK columns are generated as `MOD(i*stride, dimRows) + 1` against a dimension
whose PK is `i + 1`, so referential integrity holds **by construction** — no
orphans are possible unless you hand-edit the SQL.

**⚠ Column ids MUST be prefixed per element** (`f-`, `d1-`, `d2-`). Column ids
are not unique across elements in Sigma — a live 16-element model reuses the same
id on two — and relationship keys resolve *by id*, so a collision can join the
wrong table and still return plausible rows. `validate-datamodel-spec.py` checks
this (`relationship-key-ids-unambiguous`).

## Dialect: two expressions, nothing more

Generating rows from nothing needs a dialect-specific row source; there is no
portable form. The surface is kept to exactly two expressions so one spec emits
for both warehouses:

| | Snowflake | Databricks |
|---|---|---|
| Row source | `SELECT SEQ4() i FROM TABLE(GENERATOR(ROWCOUNT=>N))` | `SELECT id AS i FROM range(0,N)` |
| Day → date | `DATEADD('day', n, DATE '…')` | `DATE_ADD(DATE '…', n)` |

Everything else is arithmetic common to both. **Banned from the emitter**, and
`check_no_rng()` fails the build on them: `DATE_TRUNC` (the known Databricks
failure), arrays, `HASH()`, `::`, `||`, `CURRENT_DATE()`, `PI()`. Generate at the
target grain and let Sigma's dialect-free `DateTrunc()` do the rest.

**⚠ Determinism is a house rule, not a preference.** No RNG anywhere, so reruns
are row-identical — `sigma-cohort-builder-app` puts one SQL string on two element
ids and needs them to match. It also means the data can be computed in Python
before publishing.

**⚠ Weighted categoricals use `MOD(i*stride, N)` with a stride COPRIME to N**, so
the residue is a permutation of `0..N-1` and proportions land exactly at any row
count. The older fixed-modulus form drifts badly: a requested 45/35/20 came out
**58/29/13** at N=120.

## Realism is the point

Uniform noise makes a demo look broken — flat trends, rectangular cross-tabs.
Each measure composes multiplicatively:

```
value = base × Π factors × trend(t) × seasonAnnual(t) × seasonWeekly(dow)
              × categoryEffect × noise(i)        then floor + round
```

so every knob reads as "a percentage of the level" regardless of `base`. The
noise term is **mean-centred** — a raw `ABS(SIN())` adds a systematic +64% to the
mean, which would make a "7% noise" setting silently shift the level.

**Known imperfection:** category effects land a few points off spec (a requested
1.22× came out 1.33×) because the noise term and the categorical residue are both
functions of `i` and correlate weakly. Check the realized numbers after
publishing rather than assuming the spec was honoured.

## Labelling — non-negotiable

Fabricated data that looks real is dangerous; this repo has the scar. Six layers,
because each is visible to a different audience and any one alone gets stripped:

1. **SQL header comment** + `DATA_SOURCE_KIND` / `SYNTHETIC_NOTE` columns — survives someone copying the SQL out
2. **Model name suffix** `(SYNTHETIC)` — visible in the model list and source picker
3. **Model description** — "FABRICATED DATA … do not use for decisions"
4. **Marker columns on every element** — these appear in the `describe` DDL, which is what the *next agent* reads before citing these numbers as fact
5. **Column descriptions** prefixed `SYNTHETIC:` carrying the generator's parameters
6. **A visible banner in any workbook** built on it — the only marker an executive actually sees

**Not** in the element `name`: that's the load-bearing formula prefix a workbook
uses, and whether punctuation survives that reference is untested.

## Handing the model to a workbook

The workbook sources the **fact** element and references **only its own columns**:

```json
{"kind":"table","source":{"kind":"data-model","dataModelId":"…","elementId":"el-fact"},
 "columns":[{"id":"w-rev","formula":"[Orders Fact/Net Revenue]","name":"Net Revenue"}]}
```

Put the 3–5 attributes the dashboard slices by (channel, segment, region) **on
the fact**, not only on a dimension. The dimensions and relationships still exist
for the semantic layer and UI exploration — this just keeps the dashboard off a
cross-element resolution path that hasn't been verified in a workbook.

Then **open it in a browser.** HTTP 200, a clean GET-back and a rendered chart are
all three compatible with wrong numbers.

## Files

- `examples/build_synthetic_star_model.py` — the canonical generator: spec + SQL →
  a publishable multi-element data model with relationships and per-element column
  id prefixes. Clone it.
- `examples/retail-orders.schema.json` — a worked 3-table star (fact + 2 dims)
  with trend, annual seasonality, weekday lift, category effects and a correlated
  margin column.
- `reference/generator-catalog.md` — every generator kind, its parameters, and its
  SQL in both dialects.
