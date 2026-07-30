# `brief.json` — the contract

`intake-artifacts.py` writes the skeleton; the agent fills `observed`, `stated`
and `decisions`; `validate-brief.py` gates it; `sigma-company-dashboard` consumes
it. Version it with the build — commit the brief next to the generator so a
rebuild reproduces the same decisions.

```
{
  specVersion: 1
  generatedAt: "YYYY-MM-DD"
  company:   { name, domain, origin }
  artifacts: [ … ]            # written by intake-artifacts.py; don't hand-edit
  observed:  { … }            # FORM, from images only
  stated:    { … }            # FUNCTION, from transcripts only
  decisions: { … }            # what gets built; every entry provenanced
  piiFlags:  [ … ]            # every one resolved before the gate
  needsInput:[ … ]            # every one answered before the gate
  confirmedBy: "name — YYYY-MM-DD"     # set at the gate, not before
}
```

## `artifacts[]` — written by the script

| Field | |
|---|---|
| `id` | `a1`, `a2`, … — what every `source.artifact` cites |
| `path` | keep artifacts in a gitignored `artifacts/` folder |
| `kind` | `image` · `pdf` · `transcript` · `av` · `needs-conversion` · `unknown` |
| `readable` / `readWith` | `false` + `null` means fix it or drop it; never describe it from the filename |
| `fills` | `observed` for image/pdf, `stated` for transcript — the FORM/FUNCTION split, mechanically |
| `flags` | `needs-crop` · `low-resolution` · `full-page-scroll-capture` · `oversize` · `pii-present` · `pages-required` · `no-speaker-labels` · `unreadable` · `needs-conversion` |
| `width`/`height`, `pages`, `lines`/`words`/`estimatedMinutes` | whatever applies |
| `signals` | transcript only — category → `[{line, text}]`; **pointers, not conclusions** |
| `piiFlags`, `numbersToConfirm` | transcript only; both get resolved with a human |

## `observed` — images only

```
layoutEvidence: { tiles: 4, charts: 2, controls: 3, tabs: ["Trend","Detail"],
                  detailTable: true, oddVisual: "cell grid, hour × lane",
                  canvas: "dark", tool: "Tableau",
                  source: {artifact,region} }
tiles:   [ { label, formatShape, hasDelta, hasSparkline, source } ]
charts:  [ { kind, xLabel, yLabel, stacked, legend: [...], source } ]
controls:[ { kind, label, source } ]
palette: [ "#1f77b4", … ]        # informs; never sets the brand kit
vocabulary: [ "lane", "dwell", "attainment" ]     # their nouns, verbatim
```

No values, no definitions, no numbers. See `screenshot-reading.md`.

## `stated` — transcripts only

```
process:  [ { step, what, actor, frequency, source } ]          # the prize
metrics:  [ { name, definition, comparison, direction, source } ]
personas: [ { role, when, decides, source } ]
cadence:  "weekly, Monday morning"
pains:    [ { what, cost: "4 hours/week", source } ]
quotes:   [ { text, source, useInWorkbook: false } ]            # keep it false
```

## `decisions` — what gets built

Every entry is `{value|name, …, origin, source?, confirmed?, rationale?}`.

| Field | Shape | Notes |
|---|---|---|
| `dataSourcing` | `{value: sample\|byod\|synthetic, …}` | `byod` needs `table`; `synthetic` needs `syntheticBannerPlanned: true` |
| `layout` | `{value: <one of six>, …}` | `app-shell` needs `writebackConnectionConfirmed`; `comparison-variance` needs `tagColumn`; `exec-brief`/`comparison-variance` need `unverifiedAcknowledged` (both are proposed, not verified, in `layouts.md`) |
| `pages` | `[{name, purpose, tabs: [...], origin, …}]` | tabs when steps share a population; pages when audiences differ |
| `kpis` | `[{name, definition, comparison, direction, format, origin, …}]` | `comparison` is **required** — every card here is comparative |
| `charts` | `[{kind, dimension, measures, purpose, origin, …}]` | |
| `filters` | `[{kind, column, default, origin, …}]` | `segmented` ≤4 values, `list` above |
| `pluginConcept` | `{value, whatItShows, dataElement, origin, …}` | operational, not a KPI reskin |
| `page2Pattern` | `{value: input-table\|cohort-builder\|both\|none, …}` | `input-table`/`both` need `writebackConnectionConfirmed` |
| `brandKit` | `{logoSource: "fetch_logo.py <domain>", accent, gradient, font, origin, …}` | canvas is always light |

## Provenance

| `origin` | means | required |
|---|---|---|
| `observed` | seen in an image | `source.artifact` (an image/pdf) + `source.region` |
| `stated` | said in a transcript | `source.artifact` (a transcript) + `line` \| `timestamp` \| `quote` |
| `asked` | the human told us directly | — |
| `inferred` | the agent's judgement | `confirmed: true` |
| `default` | a repo default | `confirmed: true` |

`source` may be a single object or a list. Cross-kind citations fail: an
`observed` claim citing a transcript, or a `stated` claim citing an image, is an
error, not a warning — it means the FORM/FUNCTION split was violated and the claim
is probably invented.

## `piiFlags[]` and `needsInput[]`

```
piiFlags:  [ { artifact, kind, count, lines|region,
               resolution: "cropped"|"excluded"|"confirmed-non-sensitive", resolved: true } ]
needsInput:[ { code, question, answer } ]      # `answer` (or resolved:true) closes it
```

Both must be fully resolved before `validate-brief.py` passes without
`--pre-gate`. `confirmedBy` is the last thing set, by a human, at the gate.

## Checks (`validate-brief.py`)

`required-fields` · `artifact-refs-resolve` · `origin-present` ·
`source-locator-present` · `inferred-confirmed` · `no-number-from-image` ·
`no-definition-from-image` · `layout-in-catalog` · `layout-prerequisites` ·
`page2-consistency` · `data-sourcing-valid` · `kpis-comparative` ·
`pii-resolved` · `needs-input-resolved` · `human-confirmed`

Warnings (never blocking): `form-without-function`, `function-without-form`,
`needs-crop`, `low-resolution`, `full-page-scroll-capture`, `verbatim-on-surface`.
