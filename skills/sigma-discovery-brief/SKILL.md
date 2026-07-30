---
name: sigma-discovery-brief
description: >-
  BUILDING BLOCK — the ARTIFACT front door, and the optional FIRST step before
  sigma-company-dashboard. The user has things instead of answers: SCREENSHOTS or
  a PDF export of an EXISTING dashboard (Tableau, Power BI, Looker, Excel, another
  Sigma workbook), and/or a CALL TRANSCRIPT, meeting notes, or a Gong/Zoom/Granola
  export describing a PROCESS or FLOW. Read them and turn them into a reviewable
  `brief.json` — company, data-sourcing path, LAYOUT choice, pages/tabs, KPI list
  with comparison basis, charts, filters, plugin concept and page-2 pattern — every
  field carrying a PROVENANCE locator back to the artifact that justifies it. Then
  hand the brief to **sigma-company-dashboard**, which builds from it instead of
  interviewing the user. Use this whenever anyone says "here's a screenshot of our
  current dashboard", "recreate/rebuild this in Sigma", "here's a picture of what
  they use today", "here are the notes / the transcript from the discovery call",
  "this is the process they walked us through", "build from these artifacts", or
  attaches images/notes to a build request. THE ONE IDEA: screenshots decide FORM,
  transcripts decide FUNCTION, and neither decides the other — a tile label is a
  metric NAME, never a metric DEFINITION, and numbers read off a screenshot are the
  client's real figures seen through a downscaler, so reproduce the SHAPE and
  GENERATE the values. Encodes the artifact-triage rules (what is unreadable and
  the remedy, why a full-page capture reads blurry), the transcript signals that
  decide layout and page 2, the PII/confidentiality handling, and the provenance
  gate that stops a half-invented brief from driving a real build. Not a data
  source: it never replaces sigma-byod-data-model / sigma-synthetic-star-model.
---

# Sigma discovery brief — from screenshots and a call transcript to a build brief

The other three front doors ask the user questions. This one reads what they
already have. Point it at a folder of dashboard screenshots and call transcripts
and it produces the brief that `sigma-company-dashboard` builds from.

```
artifacts/  ──> intake-artifacts.py ──> read each one ──> brief.json ──> HUMAN ──> sigma-company-dashboard
 png · pdf      inventory + triage      Read tool,       decisions +    confirms   builds from the brief
 txt · vtt      + signal pointers       one at a time    provenance     the readout  (no re-interview)
```

## The one idea — form and function come from different files

| | A screenshot gives you | A transcript gives you |
|---|---|---|
| **Reliably** | layout shape, tile count, chart kinds, control kinds, palette, their vocabulary | the process, who looks and when, what decision it drives, the pain, metric *semantics*, cadence |
| **Never** | metric definitions, grain, data source, refresh, *why* anyone opens it | layout, tile geometry, palette, anything visual |
| **Fills in the brief** | `observed` | `stated` |

So: **screenshots decide FORM, transcripts decide FUNCTION.** Where they
disagree — the tile says "Attainment %" and the caller describes something else —
**the transcript wins on meaning, the screenshot wins on placement.** With only
screenshots you can copy a shape and every metric meaning is a guess; say so out
loud rather than shipping the guess as fact. With only a transcript, the layout is
a recommendation out of `sigma-company-dashboard/reference/layouts.md`, not
evidence — also say so.

## The four moves

1. **Triage** — `python3 scripts/intake-artifacts.py <folder> --company "<Name>"
   --domain <domain> --out brief.json`. Inventories every file, says how each must
   be read, flags the ones that will read badly, points at the transcript lines
   that carry a decision, and seeds `needsInput[]`. Decides nothing.
2. **Read them, one at a time** — the `Read` tool, per the checklists in
   `reference/screenshot-reading.md` and `reference/transcript-reading.md`. Fill
   `observed` from images only, `stated` from transcripts only.
3. **Derive `decisions`, each with a provenance locator** —
   `reference/brief-schema.md` is the field-by-field contract, and
   `reference/evidence-mapping.md` is the evidence → decision table. Then
   `python3 scripts/validate-brief.py brief.json --pre-gate`.
4. **The gate** — put a compact READOUT (not the JSON) in front of the human,
   take corrections, record `confirmedBy`, re-run `validate-brief.py` without
   `--pre-gate`. Only then hand the brief to `sigma-company-dashboard`.

## Provenance is the whole game

The failure mode of artifact-driven generation is not a crash. It's a brief that
reads as authoritative and is quietly half invented — a metric definition nobody
stated, a threshold OCR'd off a blurry tile, a layout chosen because the agent
liked it. That's the same class of failure as fabricated data that looks real,
and it gets the same treatment: label the origin of every claim.

Every object in `decisions` carries an `origin`, and `observed`/`stated` carry a
`source`:

```json
{"value":"command-center","origin":"observed",
 "source":{"artifact":"a2","region":"KPI band + left tab strip"},
 "rationale":"4 comparative tiles over a 3-tab content pane"}
{"name":"Net Revenue Retention","definition":"Sum(ending ARR of prior-year cohort) / Sum(beginning ARR)",
 "comparison":"prior-year","origin":"stated",
 "source":{"artifact":"a5","line":142,"quote":"NRR is the one the board asks about"}}
```

| origin | means | required |
|---|---|---|
| `observed` | seen in an image | `source.artifact` + `source.region` |
| `stated` | said in a transcript | `source.artifact` + `line` \| `timestamp` \| `quote` |
| `asked` | the human told us directly | — |
| `inferred` | the agent's judgement | `confirmed: true` |
| `default` | a repo default | `confirmed: true` |

`validate-brief.py` enforces all of it, including that an `observed` claim cannot
cite a transcript and a `stated` claim cannot cite an image.

## Hard rules

**⚠ Numbers read off a screenshot are never reproduced.** A figure on a tile is
two things at once: the client's real operating number, and an OCR guess made
through a downscaler. Echoing it into a demo is a data-handling problem *and* it
gets noticed the moment a stale or mis-read figure appears on screen. Take the
tile's *shape* — a currency KPI with a Δ-vs-prior badge and a sparkline — and let
the generated model produce the value. `no-number-from-image` fails a brief that
puts an image-sourced number in a `baseline` / `target` / `threshold`.

**⚠ A tile label is a metric NAME, not a definition.** "Attainment," "Utilization,"
"NRR" and "On-Time %" each have three plausible definitions per industry. The
numerator, denominator and filter come from the transcript or from the human —
never from the picture. `no-definition-from-image` enforces this.

**⚠ A screenshot of another BI tool is not a spec.** Do not port Tableau's grey
plot borders, default categorical palette, mark labels or right-hand legend
stack, and do not recreate a 40-column crosstab because it was on screen. Read
the *intent* of each region and re-express it in Sigma's idioms plus the brand
kit. A pixel-copy of a Tableau dashboard looks like a worse Tableau. (And if they
have the actual `.twb`/datasource rather than a picture of it, a real migration —
calc-field translation, not visual inference — beats this skill; Sigma's own
`tableau-to-sigma` skill does that, if it's installed. Reading a screenshot is the
path for when all you have is the screenshot.)

**⚠ Full-page captures read blurry, and the blur lands exactly on the numbers.**
An image whose long edge exceeds **1568px** is downscaled before the model sees
it, so a 3000px-tall scrolling dashboard grab loses card values and axis labels
while looking fine to the person who sent it. Ask for **section crops at 100%
browser zoom**: header, KPI band, each chart, one detail table. `intake-artifacts.py`
flags `needs-crop`, `low-resolution` and `full-page-scroll-capture` per file.

**⚠ Some artifacts cannot be read at all, and silently skipping them is the bug.**
Audio/video: ask for the text transcript the meeting tool already exported. `.heic`,
`.tiff`, `.docx`, `.pptx`: convert first — the script prints the exact command.
A PDF over 10 pages needs an explicit `pages` range on the `Read` call (20 pages
max per request). Never describe a file from its filename.

**⚠ Auto-transcription mangles numbers and domain vocabulary.** "Fifteen" and
"fifty" transcribe interchangeably; product names and metric names come back as
near-homophones. Every threshold, target and metric name lifted from a transcript
is confirmed with the human before it becomes a definition. The script lists
every currency/percent/spelled number with its line so you can put them in one
question.

**⚠ Speaker labels are unreliable.** Attribute a requirement to a role only when
the transcript says the role; otherwise the field is `origin:"inferred"` and needs
confirming. "The board asks for X" from an unidentified speaker is not a persona.

**⚠ The artifacts are the client's property.** Keep them in a gitignored
`artifacts/` folder (this repo ignores it). They do not go into a committed spec,
a commit message, a `CallText` prompt — which is a real LLM call from *their*
warehouse — or a workbook surface. No client verbatim on a shareable page, however
good the quote. Screenshots of a live dashboard routinely carry customer names and
emails in a detail tile: resolve every `piiFlags` entry (crop it, exclude the tile,
or confirm it's non-sensitive) before the brief passes the gate.

**⚠ Artifacts do not tell you whether we get the data.** A screenshot of a working
dashboard proves the client has data *somewhere*; it says nothing about a
connection we can point Sigma at. `dataSourcing` is still one of the three real
paths — `sigma-byod-data-model`, sample reshape, or
`sigma-synthetic-star-model` — and for a recreate-their-dashboard ask with no
connection, **synthetic is usually the answer**, with the SYNTHETIC banner and all
six labelling layers intact.

## What the artifacts structurally cannot answer

`intake-artifacts.py` seeds these into `needsInput[]` every time, because no
screenshot and few transcripts carry them: **grain** (one row = what),
**data-sourcing**, **comparison basis** (a tile reading "+4.2%" doesn't say
against what), **metric definitions**, **refresh + default date window**.

That is the *entire* interview left. It replaces the flagship's questions rather
than adding to them — the three questions `sigma-company-dashboard` normally asks
(data sourcing, layout, page-2 pattern) are mostly answered by the artifacts, so
**ask the residue once, as part of the brief readout.** Three questions is still
the ceiling.

## The readout — what the human actually sees

Never paste `brief.json` at a person. A compact readout, marked by origin, so
corrections are cheap:

> **Brief for Acme Freight** — from 4 screenshots + a 38-min discovery call.
>
> - **Layout:** Command Center *(observed — a2: 4 comparative tiles; a1: trend + wide lane table + a grid visual)*. You asked for no scrolling on the projector *(a5:112)*, which argues for Exec Brief — the ~1200-lane detail goes in a **tab** instead, so: one screen, detail one click away.
> - **Data:** synthetic star from the lane schema you're sending *(stated — a5:82 "I can't hand you a table for a demo")*. Carries a visible **Synthetic demo data** banner.
> - **KPIs:** On-Time % vs prior month *(a5:29)* · Cost per Mile vs quarterly plan *(a5:39)* · Avg Dwell Hours vs prior month, 4h goal / 6h red *(a5:42, 48 — read back and confirmed, since both numbers were spoken as words)* · Claims Rate vs prior year *(a5:43)*
> - **Page 2:** rate-reforecast input table + change log *(stated — a5:53 "export the lane summary to Excel and reforecast the rates", a5:95)*
> - **Plugin:** Lane Dwell Clock — hour × lane cells shaded against the 4h goal *(observed — a3; you already built this shape and it's the part the regionals use)*
> - **Not in the artifacts, need from you:** grain (one row = a shipment or a lane-day?), whether a reschedule counts as late *(contested at a5:34, no owner at a5:98)*, default date window.
> - **Dropped on purpose:** the real figures on your KPI tiles *(we generate values, we don't reproduce yours)*, the consignee-name column *(cropped)*, and your Tableau palette.
>
> Correct anything and I'll build.

`examples/brief.example.json` is this brief in full, and it passes the gate — read
it before authoring your first one.

Then record `confirmedBy` and go.

## Files

- `reference/screenshot-reading.md` — the per-image extraction checklist, the
  region taxonomy, the other-BI-tool translation table, and what not to copy.
- `reference/transcript-reading.md` — mining a call for the process, the metrics,
  the personas and the cadence; how a described flow becomes pages and tabs.
- `reference/evidence-mapping.md` — the evidence → decision table (which signal
  picks which layout, which phrase picks page 2) and the conflict rules.
- `reference/brief-schema.md` — the `brief.json` contract, field by field.
- `examples/discovery-call.transcript.txt` — a synthetic worked transcript
  (labelled; no real customer), with the signal lines the script finds.
- `examples/brief.example.json` — the brief derived from it, gate-clean.
- `scripts/intake-artifacts.py`, `scripts/validate-brief.py`.

**Status:** the triage script and the gate are verified by
`examples/` round-tripping through both, on this repo's own example artifacts. The
extraction checklists are **proposed** — tighten them after the first two real
calls, and record what a real Tableau screenshot actually gave up.
