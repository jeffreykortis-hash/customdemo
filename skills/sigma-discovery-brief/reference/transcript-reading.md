# Reading a call transcript

Transcripts fill `stated` — **function only**. Nothing here produces a layout.

`intake-artifacts.py` prints signal hits with line numbers per category. Those are
**pointers**, not conclusions: go read the cited lines with their surrounding turns
and decide. A signal hit on `\bbudget\b` is as likely to be someone's aside about
procurement as a write-back requirement.

## The four things worth mining

### 1. The process — this is the prize

A transcript's most valuable content is the sequence of steps somebody actually
performs. Capture it as an ordered list, each step with the line it came from:

```json
{"step":3,"what":"exports the lane summary to Excel and reforecasts rates",
 "actor":"pricing analyst","frequency":"weekly",
 "source":{"artifact":"a5","line":162}}
```

Then read the shape of the sequence:

| Shape of the described process | What to build |
|---|---|
| Steps 1..N over the **same population**, one audience | ONE page, a `tabbed-container` with a tab per step |
| Steps split across **different audiences** ("then it goes to the controller") | separate pages, one per audience |
| A step that **exports, edits, and sends back** | **page 2 = `sigma-input-table-app`** (decisive) |
| A step that **narrows to a list of records and works it** | **page 2 = `sigma-cohort-builder-app`** (decisive) |
| A step that is "check whether anything is off, then dig in" | an exceptions element + a drill path; a `comparison-variance` signal |
| No process at all — only "we want to see X" | no page 2; ask whether one is wanted |

**The write-back tell is a spreadsheet.** "We pull it into Excel," "I type in next
quarter's numbers," "she sends it back with her changes" — that is a data app, and
it's the single most reliable requirement in any discovery call. It also means a
**writeback-enabled connection**, which is an admin toggle you must check with
`scripts/api/list-connections.sh --writable` before promising the page.

### 2. The metrics — name, definition, comparison basis

For each metric named on the call, record all three, and mark which you actually
got:

```json
{"name":"On-Time Delivery","definition":"deliveries arriving <= appointment window / all deliveries",
 "comparison":"prior-month","origin":"stated","source":{"artifact":"a5","line":88}}
```

- **Name** is usually stated. Their exact word — "attainment," not "% of quota."
- **Definition** is *sometimes* stated, often half-stated ("on-time means within
  the window" — which window?). Half a definition is `needsInput`, not a guess.
- **Comparison basis** is the one people forget to ask about, and every card in
  this repo needs it. Prior period, prior year, plan/budget, or a target.
- **Direction** — is up good? Cost, churn, dwell time and claims rate all render
  backwards if you assume up-is-good. Record it.

**⚠ Never lift a number from a transcript without confirming it out loud.**
Auto-transcription renders "fifteen" and "fifty" interchangeably, and a
mis-transcribed threshold becomes a wrong metric definition that survives all the
way to a published model. The script lists every currency amount, percentage and
spelled-out number with its line — put them all in one confirmation question.

### 3. Personas and cadence

Who opens it, when, and to decide what. This sets the layout as much as the
metric count does:

- "the board, monthly" → `exec-brief`
- "the ops team, all day on a wall screen" → `command-center`
- "analysts pull from it and reshape it" → `analyst-detail`
- "our customers see it" → `product-surface`
- "I check it every Monday morning before the pipeline call" → default the date
  control to week-grain, and there's an implied "since last Monday" comparison

Attribute a persona only when the transcript names the role. An unattributed
"they want…" is `origin:"inferred"`.

### 4. The pain — what makes this demo land

"It takes me four hours every Monday." "Three different systems." "Nobody trusts
the number." "By the time I see it, it's a week old." Record these with their
lines. They do two things: they pick which surface goes first (the page that kills
the four hours), and they tell you what the AI-summary text should be *about*.

**⚠ Their words, not their voice.** A pain point informs what you build; a
verbatim quote does not go on a workbook surface. `validate-brief.py` warns on any
quote marked `useInWorkbook`.

## Reading order

1. Skim the whole file first — `Read` it in full if it's under a few thousand
   lines. Signals out of context mislead.
2. Extract the **process** before anything else; everything else hangs off it.
3. Extract metrics, then personas/cadence, then pains.
4. Note the **vocabulary** list (their nouns) — it goes into element titles.
5. List what was *asked about and dodged*: budget, timeline, data access,
   "who owns this number." Those belong in the readout as open items, not in the
   brief as decisions.

## What never leaves a transcript

- **Layout.** Nobody describes grid geometry on a discovery call.
- **Exact numbers**, until confirmed (above).
- **PII.** Names, emails, phone numbers, account IDs — flagged by the script,
  resolved before the gate, never carried into a spec or a `CallText` prompt.
- **Attribution you didn't get.** No speaker label means no persona.
