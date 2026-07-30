# Reading a dashboard screenshot

Images fill `observed` — **form only**. Nothing on this page produces a metric
definition, a number, or a data source.

Read **one image per `Read` call** and write down what you saw before opening the
next. Reading four crops in one turn and then summarizing produces a blend: tiles
from one image, palette from another, and no locator for either.

## Region taxonomy

Walk the image top-left to bottom-right and classify every region as exactly one
of these. The names match the repo's element kinds, so the brief maps straight to
a generator.

| Region | What to record | Feeds |
|---|---|---|
| **masthead** | logo present? position · title text · gradient vs flat vs image · dark or light | `decisions.brandKit`, header standard |
| **control bar** | each control's *kind* (date range, list, segmented, text) and its label — not its value | `decisions.filters` |
| **KPI band** | tile count · per tile: label, value format shape (`$1.2M` → `$.3~s`, `94.2%` → percent, `1,284` → count), is there a Δ badge, is there a sparkline | `decisions.kpis` |
| **primary chart** | chart kind · what's on each axis (by label) · stacked/grouped · legend entries | `decisions.charts[0]` |
| **secondary charts** | same, plus position relative to the primary | `decisions.charts[1..]` |
| **detail table / pivot** | column labels in order · row dimension(s) · any conditional formatting · roughly how many columns | `decisions.charts` (pivot) |
| **bespoke / odd visual** | anything that isn't a standard chart — a gauge cluster, a map, a grid-of-cells, a timeline | `decisions.pluginConcept` ← **the strongest plugin signal there is** |
| **tabs / nav** | tab labels in order, and which one is active | `decisions.pages`, tabbed container |
| **chrome** | the source tool's own furniture — toolbars, "Edit" buttons, breadcrumbs, watermarks | identifies the tool; **never** reproduced |

## Per-image checklist

1. **Which tool is this?** Tableau, Power BI, Looker, Excel, Google Sheets, a
   Sigma workbook, a slide? Say it in the artifact note. It changes how you read
   everything below, and Sigma workbook screenshots are the one case where the
   layout can be copied nearly as-is.
2. **Is it a crop or a full page?** A full page tells you layout and nothing
   legible; a crop tells you detail and nothing about placement. Record which.
3. **Count the tiles and the charts.** Counts drive the layout decision far more
   than any single element does — see `evidence-mapping.md`.
4. **Read labels, never values.** Record `"Revenue"`, `"$1.2M"` → *"currency,
   3-significant-figure scale"*. Do not record `1.2` anywhere.
5. **Note the palette** as observed hex-ish colors (2–6 of them) and whether the
   canvas is light or dark. This informs, but does not set, the brand kit — the
   brand kit comes from `fetch_logo.py` + the real brand colors, and the canvas
   is **always light** in this repo regardless of what their tool did.
6. **Note their vocabulary verbatim** — "lane," "dwell," "book of business,"
   "attainment." Their words go in the generated titles. This is one of the two
   highest-value things a screenshot gives you.
7. **Flag PII on sight** — customer names, emails, phone numbers, account numbers,
   employee names in a detail tile. Add a `piiFlags` entry with the region.
8. **Say what you could not read.** Blurry axis, clipped legend, a truncated
   column header. An honest gap is a question; a confident guess is a defect.

## Translating another tool's idioms

| What you see (their tool) | What to author in Sigma |
|---|---|
| Grey plot borders, default blue/orange series, right-hand legend stack | drop entirely; theme comes from the brand kit |
| A plain big-number tile | a **comparative gradient KPI card** — value + Δ badge + sparkline (this repo has no plain-number KPIs) |
| A 30+ column crosstab | a pivot with the 6–10 columns anyone was actually described using; put the rest in the base table |
| Tableau parameter / Power BI slicer | a Sigma control (`segmented` for ≤4 values, `list` above that) |
| A KPI whose Δ is a separate tile | fold into one card's `comparison` block |
| Traffic-light conditional formatting | keep — but confirm the thresholds with a human; they are never legible enough to trust |
| Sheet tabs across the bottom | a `tabbed-container` if the tabs share a population, separate pages if the audiences differ |
| An embedded logo image | `fetch_logo.py` on their real domain; never trace the one in the screenshot |
| A gauge / map / timeline / custom viz | a **bespoke plugin** — propose it as concept #1, since they've already told you they want it |

## What never leaves a screenshot

- **Values.** See the hard rule in `SKILL.md`.
- **Definitions.** A label names a metric.
- **Grain, source, refresh.** Not visible, ever.
- **Thresholds.** A red cell means *something* crossed *something*. Ask.
- **The other tool's chrome, palette, or default type sizes.**
- **PII**, in any form, including "the first customer in the list."
