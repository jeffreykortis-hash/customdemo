# Evidence → decision

The mapping from what an artifact shows to what goes in `decisions`. Read down;
the first strong signal wins, and **decisive** rows beat judgement rows even when
several judgement rows point elsewhere.

## Layout

`sigma-company-dashboard/reference/layouts.md` owns the six layouts and their
geometry. This table is how artifacts answer its decision question — so you
usually don't have to ask it.

| Evidence | Where from | Layout |
|---|---|---|
| A described step where someone **exports, edits, and returns** values | transcript | **`app-shell`** (decisive) |
| A described step that **narrows a population to a working list** | transcript | **`app-shell`** (decisive) |
| A **period / scenario / version tag** visible as a column or a picker; or "budget vs actual", "this year vs last" as the *organizing* idea | either | **`comparison-variance`** (strongest single signal) |
| Audience is **the board / an exec / a wall screen**, ≤4 headline tiles, no detail table on screen | either | `exec-brief` |
| **Customer-facing or embedded**; a card-shaped repeated entity set at ≤8 values | either | `product-surface` |
| **7+ tiles**, 4+ dimension controls, a wide crosstab, "analysts pull from it" | either | `analyst-detail` |
| An **odd non-standard visual** on screen, an ops audience, many measures | either | `command-center` (default for a POV) |
| Nothing decisive | — | `command-center`, `origin:"default"`, `confirmed` required |

**Counts beat impressions.** Tile count and control count from a screenshot are
the two most reliable layout inputs; "it looked busy" is not evidence.

**No date column mentioned anywhere** rules out `exec-brief` and `analyst-detail`
(no trend, no period comparison) — the same rule as in `layouts.md`.

## Data sourcing

| Evidence | `dataSourcing` |
|---|---|
| "point it at `<DB>.<SCHEMA>.<TABLE>`", a named warehouse + granted access | `byod` → **`sigma-byod-data-model`** |
| A working dashboard in a screenshot, **no connection discussed** | `synthetic` → **`sigma-synthetic-star-model`** + SYNTHETIC banner |
| "we don't have the data yet", "mock it up", a schema/DDL pasted | `synthetic` |
| A generic POV in a familiar retail/CPG shape, no client data, no schema | `sample` → flagship move 1 |

**⚠ A screenshot of a working dashboard is not data access.** This is the most
common wrong inference in the whole flow: their dashboard obviously has data
behind it, and we still have nothing to point Sigma at. Default a
recreate-this-dashboard ask to **synthetic**, and say that's what you did.

## Page 2

| Evidence | `page2Pattern` |
|---|---|
| Spreadsheet round-trip, "reforecast", "adjust", "submit", "approve", "sign off" | `input-table` |
| "filter down to", "the ones that", "target list", "book of business", "saved list" | `cohort-builder` |
| Both described, by different people | `both` (one page each) |
| No process described, or purely read-only monitoring | `none` — and say so |

## KPI cards

Each `decisions.kpis[]` entry needs: `name` (their word), `definition`,
`comparison`, `direction`, `format`, and provenance.

| Evidence | Fills |
|---|---|
| Tile label | `name` — `observed` |
| Value's *shape* (`$1.2M`, `94.2%`, `1,284`) | `format` (`$.3~s`, percent, count) — `observed` |
| A Δ badge / arrow on the tile | there IS a comparison, but **not what it's against** |
| Someone saying what it's measured against | `comparison` — `stated` |
| Someone defining the numerator/denominator/filter | `definition` — `stated` |
| A cost / churn / dwell / claims-style metric | `direction: "down-is-good"` — get it right or the badge renders backwards |

**Never** `definition` or any number from an image. `validate-brief.py` fails both.

## Plugin concept

| Evidence | Then |
|---|---|
| A non-standard visual on screen (gauge cluster, map, cell grid, timeline) | **that's concept #1** — they've already told you what they want |
| A process described spatially ("we watch the lanes fill up during the day") | a concept built on that mental model |
| Nothing | propose 2–4 domain concepts and let the human pick, per the flagship |

The plugin is still an *operational* visual, never a KPI reskin, and it still gets
re-picked in the editor panel after the POST — see `sigma-company-dashboard`.

## Brand kit

| Evidence | Then |
|---|---|
| A logo in the screenshot | run `scripts/fetch_logo.py <domain>` — **never trace the screenshot's logo** |
| Their palette on screen | informs the accent colors; confirm against their real brand colors |
| A dark canvas in their tool | **ignored** — this repo's canvas is light (dark breaks control dropdowns and input tables) |
| Their vocabulary | goes into element titles verbatim |

## Conflict rules

1. **Transcript wins on meaning; screenshot wins on placement.** A tile labelled
   "Attainment %" over a caller describing bookings-to-quota: the definition is
   the caller's, the position is the tile's.
2. **A human overrides both.** Anything corrected at the gate becomes
   `origin:"asked"` and outranks the artifacts.
3. **Later artifact does not win by default.** If two screenshots disagree, they're
   probably different states of the same dashboard (different filters, different
   tab) — record both and ask, rather than picking the newer file.
4. **Absence is not evidence.** No trend chart in the crops you were sent does not
   mean they don't have one. Ask before dropping a whole region.
5. **When a decisive signal and a judgement signal conflict**, decisive wins and
   the conflict goes in the readout so the human can overrule it.
