# Layout catalog — six shapes, and how to pick one

This repo used to have four different page layouts, each written up as *the*
shape for its own skill, with no way to choose between them. This file is the
catalog and the decision. Each layout's **mechanics** stay in the skill that owns
it; **when to choose it** lives here.

Pick a layout **before drafting page 1**, ask the user, and record the choice at
the top of the generator as `LAYOUT = "<name>"` so a rebuild reproduces it.

## Decision table

Read down; the first strong signal wins. `app-shell` and `comparison-variance`
have decisive signals — the rest are judgement.

| Signal | Layout |
|---|---|
| Users will **enter / adjust / save** values (write-back, planning, cohort building) | **`app-shell`** (decisive) |
| A period / scenario / version tag column exists, or two named comparable entities | **`comparison-variance`** (strongest single signal) |
| Board, exec readout, or wallboard; 1–4 headline measures; "one screen" | **`exec-brief`** |
| Embedded / customer-facing; "make it look like a product"; a low-cardinality entity set that reads as cards | **`product-surface`** |
| 7+ measures, 4+ analytical dimensions, "people will pull from this table" | **`analyst-detail`** |
| Operational monitor, a bespoke plugin in scope, an agent rail wanted | **`command-center`** (default for a company POV) |

Supporting signals: **no date column** rules out `exec-brief` and `analyst-detail`
(no trend, no period comparison). Dimension cardinality steers the detail block —
≤8 reads as cards (`product-surface`), 9–50 as charts/pivot (`analyst-detail`),
>50 wants a table or heatmap (`command-center`).

## The question to ask

Once, after recon, before page 1. State the recommendation **with its evidence**;
`"recommended"` is a valid one-word answer so the fast path survives.

> **Which layout?** Your data has 6 measures, a monthly date column spanning 24
> months, a `PERIOD_NAME` Current/Prior tag, Segment at 5 values and Product at
> 10 — and no write-back. I recommend **Command Center**: enough measures for a
> full KPI band, a date column for the trend, and a tab slot for the bespoke
> plugin.
>
> **A.** Exec Brief — one screen, no scroll, no detail table
> **B.** Command Center *(recommended)* — KPI cards + tabbed trend/plugin/detail + agent rail
> **C.** Analyst Detail — filter bar, two KPI rows, trend, a wide pivot people pull from
> **D.** Comparison / Variance — A vs B with a delta rail and a variance chart (your `PERIOD_NAME` supports this)
> **E.** App Shell — tabbed data app with input tables (pick this if users will *enter* values)
> **F.** Product Surface — masthead, repeated cards, sidebar; reads as an embedded product
>
> Reply with a letter, or "recommended".

Follow-ups, only when the answer needs more input — and **merge these with the
page-2 and plugin-concept questions the flagship already asks**; three questions
total is the ceiling:

- **D** → "Compare across what — periods, scenarios, or two named entities? Which column carries it?"
- **E** → fold into the existing `sigma-input-table-app` vs `sigma-cohort-builder-app` question; don't ask twice.
- **F** → "Which column is the card entity, and is there an image-URL column?"
- **A** → "Which 4 metrics are the headline?"

If the user says "just build it" or doesn't answer, **use the recommendation and
say which one you used** — never silently.

---

## 1. `exec-brief`

Board / exec readout / wallboard. One screen, no scrolling, no detail table.
1–4 headline measures.

```
┌─ header (compact: title + 2 inline controls, no filter row) ──────┐
│  KPI    KPI    KPI    KPI                                          │
├────────────────────────────┬──────────────────────────────────────┤
│  hero trend                │  AI insight                          │
└────────────────────────────┴──────────────────────────────────────┘
```

24-col: header `1/6` (title `1/16`, date `16/21`, grain `21/25`) · KPI row `6/16`
(4 tiles × 6 cols) · hero trend `1/17` × `16/34` · AI insight `17/25` × `16/34`.
Terminates ~row 34 = one screen.

**Status: proposed, geometry unverified.** The whole premise is "no scroll,"
which only a browser can confirm. Verify before promoting.
**Owner:** none yet — build as a trimmed `command-center` (drop the pivot tab and
the agent rail).

## 2. `command-center`

Operational monitor, and the default for a branded company POV. Many measures, a
bespoke plugin, an agent.

```
┌─ gradient header + logo ──────────────────────────────────────────┐
│  KPI card   KPI card   KPI card   KPI card        (comparative)   │
├─ filter / color-by bar ───────────────────────────────────────────┤
│  ┌─ tabbed container ───────────────┐  ┌─ agent rail ──────────┐  │
│  │ trend │ plugin │ detail pivots   │  │ (same row range)      │  │
│  └──────────────────────────────────┘  └───────────────────────┘  │
└───────────────────────────────────────────────────────────────────┘
```

24-col: header `1/5` · KPI cards `5/13` · filter bar `13/20` · tabbed container
`gridColumn 1/18` × `gridRow 20/60` · agent rail `18/25` × `20/60`.

**Status: verified.** **Owner:** `sigma-company-dashboard` (mechanics + the
`⚠ never nest a GridContainer inside a Tab` rule).
**Realized by:** `examples/build_company_command_center.py`.

## 3. `analyst-detail`

Exploration. Many dimensions, many metrics, and a wide table people actually pull
from and reshape.

```
┌─ header (logo, title, date range, grain) ─────────────────────────┐
├─ filter bar (cascading list controls) ────────────────────────────┤
│  KPI KPI KPI KPI KPI KPI              (headline)                  │
│  KPI KPI KPI KPI KPI                  (rates / secondary)         │
├───────────────────────────────────────────────────────────────────┤
│  primary time-series, grain-controlled                            │
├───────────────────────────────────────────────────────────────────┤
│  wide detail pivot — every metric, dimensions as rows             │
└───────────────────────────────────────────────────────────────────┘
```

24-col: header `1/4` · filters `4/7` · KPI row 1 `7/15` · KPI row 2 `15/23` ·
trend `23/38` · pivot `38/62` · base table below.

**Status: verified.** **Owner:** `branded-dashboard-format` (`reference/format.md`).

## 4. `comparison-variance`

Budget vs actual, YoY, region A vs B, scenario vs baseline. Choose it when a
period/scenario tag column exists.

```
┌─ header ──────────────────────────────────────────────────────────┐
├─ A / B picker bar ────────────────────────────────────────────────┤
│  side A          │ Δ delta rail │          side B                 │
├───────────────────────────────────────────────────────────────────┤
│  variance chart (diverging / waterfall)                           │
├───────────────────────────────────────────────────────────────────┤
│  variance-ranked detail table                                     │
└───────────────────────────────────────────────────────────────────┘
```

24-col: header `1/4` · picker `4/7` · side A `1/11` × `7/23` · delta rail `11/15`
× `7/23` · side B `15/25` × `7/23` · variance chart `1/25` × `23/38` · detail
`1/25` × `38/62`.

**Status: proposed, no exemplar yet — say so if you offer it.** Paired KPI columns
flanking a centre rail are untested, and this is exactly where the uniform-card
rule bites: cards must use `gridTemplateRows:"repeat(N,1fr)"`, never `"auto"`, or
one side sits taller than the other.

## 5. `app-shell`

Interactive data app — write-back, planning, cohort building. Chosen whenever
users *enter* values.

```
┌─ header ──────────────────────────────────────────────────────────┐
│  (optional KPI strip — comparative, flat-stat)                    │
├─ tabbed container ────────────────────────────────────────────────┤
│  Builder │ Visualize     input tables · buttons · agent           │
└───────────────────────────────────────────────────────────────────┘
```

24-col: header `1/5` · optional KPI strip `5/13` · tabbed container `1/25` ×
`7/60`.

**Status: verified.** **Owners:** `sigma-input-table-app` (scenario/planning),
`sigma-cohort-builder-app` (segmentation).
**Hard requirement:** a LIGHT theme base — dark renders input tables and dropdowns
white-on-white. And the input tables need a **writeback-enabled connection**.

## 6. `product-surface`

Customer-facing or embedded. "Make it look like a real app." A low-cardinality
entity set that reads as cards.

```
┌─ masthead (logo left, profile/filters right) ─────────────────────┐
│  card   card   card   card            (repeated, 1fr 1fr 1fr 1fr) │
├──────────────────────────────┬────────────────────────────────────┤
│  primary chart (2/3)         │  repeated sidebar list (1/3)       │
├──────────────────────────────┴────────────────────────────────────┤
│  ─────────────────── divider ───────────────────                  │
│  detail band with status pills                                    │
└───────────────────────────────────────────────────────────────────┘
```

24-col: masthead `1/6` · card row `6/16` with `gridTemplateColumns:"1fr 1fr 1fr 1fr"`
· chart `1/17` × `16/38` · sidebar `17/25` × `16/38` · divider `38/39` · detail
band `1/25` × `39/58`.

**Status: partly verified** from the styling examples.
**Owner:** `sigma-workbook-styling`.
**⚠ The repeat toggle is the one fragile, partly-UI piece** — clone the repeated
blocks from `sigma-workbook-styling/examples/cold-provisions.json`, never author
them from scratch, and note the UI step so a rebuild doesn't lose it.

---

## Constraints that cut across layouts

- **Never nest a `<GridContainer>` inside a `<Tab>`.** Tabs take bare
  `<LayoutElement>` children. This means `product-surface`'s repeated card row —
  and any container-wrapped gradient card — **cannot** go in a tab. Verify before
  documenting a tabbed variant of layouts 1, 3, 4 or 6.
- **Uniform card geometry:** `gridTemplateRows:"repeat(N,1fr)"`, never `"auto"` —
  `auto` sizes rows to content, so a longer value string makes one card taller.
- **`layout` is a top-level workbook field.** A `layout` under a page is silently
  discarded; `scripts/validate-spec.py` catches it.
- **HTTP 200 is not evidence.** Cross-element column references fail at render
  time, not at POST. Every layout ends with a human confirming on screen.
