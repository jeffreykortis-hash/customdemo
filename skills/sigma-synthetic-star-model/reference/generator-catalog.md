# Generator catalog

Every column in a schema spec has a `generator`. Each kind below is defined once
in `scripts/_synth.py` in two forms — a SQL expression and the same computation
in Python — so the simulator and the warehouse cannot drift.

`i` is the row counter. `N` is the table's row count. All strides are assigned
from a prime pool and **persisted into the spec**, so they're stable across
regenerations and visible to a reviewer.

## Kinds

| kind | parameters | SQL (both dialects unless noted) |
|---|---|---|
| `id` | `prefix`, `pad`, `start` | `CONCAT('ORD-', LPAD(CAST(i + 1 AS STRING), 6, '0'))` |
| `sequence` | `start` | `i + 1` |
| `fk` | `table`, `column`, `dimRows` | `MOD(i * stride, dimRows) + 1` |
| `date` | `days` | SF `DATEADD('day', DAY_IX, DATE '…')` · DBX `DATE_ADD(DATE '…', DAY_IX)` |
| `categorical` | `values[{value,weight}]` | `CASE WHEN R_col < cut1 THEN 'A' … END` |
| `vocabulary` | `values[{value}]` | identical emission; separate kind so the parser can *ask* for real names |
| `boolean` | `probability` | `CASE WHEN R_col < p*N THEN TRUE ELSE FALSE END` |
| `measure` | `base`, `factors`, `trend`, `seasonality`, `categoryEffects`, `noise`, `round`, `floor`, `cap` | the composed product below |
| `correlated` | `of`, `rate{column,byValue,default}`, `residual` | `ROUND(<other> * CASE <col> … END * noise, 2)` |
| any | `nullRate` | wraps: `CASE WHEN NR_col < r*N THEN NULL ELSE <expr> END` |

`fk` guarantees referential integrity **by construction**: it cycles over the
dimension's own row count, and the dimension's PK is `i + 1`. No orphans are
possible unless the SQL is hand-edited.

## Why `MOD(i * stride, N)` and not `MOD(i * stride, 1000)`

With a stride **coprime to N**, `MOD(i*stride, N)` is a *permutation* of
`0..N-1` — every residue occurs exactly once, so a 45/35/20 split lands exactly
at any row count. The fixed-modulus form samples N points of a 1000-wide space
and drifts: a requested 45/35/20 came out **58/29/13** at N=120 (verified live).

Each stride-driven column gets a **distinct** stride, including null masks — two
columns sharing a stride would correlate. That's the repo's coprime-stride
decorrelation idiom, promoted from a magic number to a named, persisted field.

## The measure composition

```
value = base
      × Π factors(other columns)
      × trend(t)              (1 + pctPerPeriod * DAY_IX)
      × seasonAnnual(t)       (1 + amp * SIN(2π/periodDays * DAY_IX + phase))
      × seasonWeekly(dow)     CASE MOD(DAY_IX + anchorDow, 7) WHEN 5 THEN 1.34 …
      × categoryEffect        CASE CHANNEL WHEN 'Mobile app' THEN 1.22 …
      × noise(i)
then GREATEST(floor, LEAST(cap, ROUND(·, n)))
```

Multiplicative throughout, so every knob reads as "a percentage of the level"
regardless of `base`. `2π/periodDays` is baked at compile time because `PI()`
isn't portable.

**The noise term is mean-centred:**

```
1 + amplitude * ((ABS(SIN(i*k)) - 0.63662) / 0.30776)
```

`E|sin| = 2/π = 0.63662`, `σ|sin| = √(½ − 4/π²) = 0.30776`. Without the
recentring, a raw `ABS(SIN())` adds a systematic **+64%** to the mean — so a "7%
noise" knob would silently move the level. Each measure gets a distinct `k` from
a tuned set so two measures never wobble in lockstep.

**Known imperfection:** because both the noise term and a categorical's residue
are functions of `i`, they correlate weakly. A requested 1.22× category effect
measured 1.33× in a live 5000-row run. Check realized numbers rather than
assuming the spec was honoured.

## Emitted structure

```sql
WITH g    AS ( <row source> ),                      -- the only dialect-specific FROM
     ix   AS ( SELECT i, DAY_IX, R_* residues … ),  -- computed ONCE
     dims AS ( ids, dates, categoricals, booleans ),
     meas AS ( measures, which may reference dims columns by name )
SELECT <projection: correlated cols, null masks, SYNTHETIC markers> FROM meas
```

The layering isn't decoration: referencing a column alias defined earlier in the
**same** SELECT list ("lateral column alias") isn't reliably portable — Snowflake
allows it, older Databricks runtimes don't. Layering removes the dependency and
makes the SQL readable, which matters because the SQL is itself a deliverable.

## Banned constructs

`check_no_rng()` fails the build on any of these, rather than trusting a comment:

`RANDOM` · `RAND(` · `UNIFORM(` · `NORMAL(` · `RANDSTR` · `SHUFFLE` ·
`CURRENT_DATE` · `CURRENT_TIMESTAMP` · `NOW(` · `GETDATE` · `LOCALTIMESTAMP` ·
`UUID_STRING` · `HASH(` · `DATE_TRUNC` · `ARRAY_CONSTRUCT`

The RNG and time functions break determinism (reruns stop being row-identical).
`HASH()` is deterministic but **differs between Snowflake and Databricks**, so it
would silently produce different data per dialect. `DATE_TRUNC` and
`ARRAY_CONSTRUCT` break Databricks outright.
