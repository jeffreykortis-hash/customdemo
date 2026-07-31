# API in and out of a built workbook

Two different things people mean by "API and the dashboard", both in scope when
you build a demoable asset:

1. **Actions OUT of the workbook** — a button in the dashboard calls an external
   HTTP endpoint (Sigma's **Call API** action). Makes a demo *do something*.
2. **Calls INTO the workbook** — an API client or agent queries the dashboard's
   elements after it exists. Makes the asset *addressable*.

Direction 2 is fully verified and wrapped by scripts here. Direction 1 is real
but its **spec shape is NOT yet verified from code** — read the honest status below
before promising it in a build.

---

## The rule that governs all action work

**Every malformed action fails as the same masked `Invalid kind: "button"`.** An
unknown effect NAME and a known effect MISSING A REQUIRED FIELD are byte-identical
in the response. Verified: `{"effect":"navigate","page":"pg"}` (real effect, wrong
field) and `{"effect":"zzz-not-real"}` return the same error.

**Therefore you cannot discover an action shape by guessing.** A 16-candidate name
sweep produced 16 identical errors and taught nothing. The only reliable method is
to **clone the shape from a workbook that already uses it**:

```
scripts/api/extract-action-shapes.sh                     # harvest across the org
scripts/api/extract-action-shapes.sh --effect call-api    # one effect
scripts/api/extract-action-shapes.sh --workbook <id>      # one workbook
```

It prints one canonical JSON example per distinct effect. Paste it into a generator.

## Verified effect vocabulary

Harvested from live workbooks and each **re-POSTed successfully** to confirm.
Note every effect object also carries `"kind": "effect"` when nested in an agent
tool step; button actions accept it with or without.

| Effect | Verified shape |
|---|---|
| `set-control-value` | `{"effect":"set-control-value","control":"<controlId>","value":{...},"selectionMode":"add"?}` |
| `clear-control` | `{"effect":"clear-control","scope":{"type":"page","page":"<pageId>"},"usePublishedValue":true}` |
| `insert-rows` | `{"effect":"insert-rows","table":"<inputTableId>","values":{...}}` |
| `open-overlay` / `close-overlay` | see `sigma-input-table-app` (modal pattern) |
| `navigate` | `{"effect":"navigate","target":{"type":"page","page":"<pageId>"}}` |
| `select-tab` | `{"effect":"select-tab","tabbedContainer":"<elementId>","selectedTab":{"type":"tab","index":0}}` |
| `open-url` | `{"effect":"open-url","openTarget":"_blank","url":"https://…"}` |
| `open-document` | `{"effect":"open-document","document":"<inodeId>","openTarget":"_self","documentType":"workbook"}` |

⚠ `navigate` takes a nested `target` object — **not** a flat `page` key. That flat
guess is exactly the failure that proves the masked-error rule above.

`select-tab` is what makes a `tabbed-container` drivable from a button, which is
how you script a guided demo walkthrough.

## Call API — status: REAL FEATURE, SPEC SHAPE UNVERIFIED

Sigma supports a **Call API** action (button → external HTTP endpoint), documented
at `help.sigmacomputing.com/docs/create-actions-that-call-api-endpoints`. It needs:

- the **Create API actions** permission to author, **Trigger API actions** to run;
- an **API connector** configured in the org.

Connectors ARE discoverable and readable from code:

```
scripts/api/list-api-connectors.sh                 # 50 exist in papercranestaging
scripts/api/list-api-connectors.sh --detail <id>   # method, url, headers, bodyParams
```

`--detail` returns each connector's method/url/headers plus `bodyParams` with a
`mode` of `static` or `dynamic` — the dynamic ones are what a workbook action maps
to a control, column, or formula at call time. Per the docs, a Call API action also
exposes three action variables downstream in the same sequence — **response data**,
**response status**, **response headers** — so the demoable pattern is
*button → Call API → `set-control-value` into a text-area control to show the response.*

**What is NOT known:** the JSON that expresses this in a workbook spec. Reasons:

- **No workbook in this org uses it.** A scan of all 92 workbooks found eight
  distinct effects; `call-api` was not among them, so there is nothing to clone.
- **Blind probing cannot resolve it** — 13 name×payload combinations all returned
  the same masked error, and so did known-good effects with wrong fields.

**Do not ship a guessed `call-api` shape.** It will fail as `Invalid kind: "button"`
and you will not be able to tell whether the name or a field was wrong.

**The unlock is one manual step, then it's permanent:**

1. In the Sigma UI, add a Button to any workbook and configure one Call API action
   against any connector (the "KM Test: Current Temp - Hard Coded" connector needs
   no dynamic params, so it's the cheapest).
2. `scripts/api/extract-action-shapes.sh --workbook <that workbook id>`
3. Paste the captured shape into this table and into your generator.

Until then, a build that promises "clicking this calls your API" is promising
something not yet reproducible from code — say so rather than shipping a dead button.

---

## Calling INTO a built workbook (verified, works today)

Every element of a created workbook is queryable over REST — this is how the
Cleveland Clinic build verified each KPI against raw SQL.

| Endpoint | Returns |
|---|---|
| `GET /v2/workbooks/{id}/pages` | page ids + names |
| `GET /v2/workbooks/{id}/pages/{pageId}/elements` | every `elementId`, type, name |
| `GET /v2/workbooks/{id}/queries` | **the warehouse SQL Sigma generates per element** |
| `POST /v2/workbooks/{id}/export` → `GET /v2/query/{queryId}/download` | element data as CSV/JSON |

Wrapped as:

```
scripts/api/workbook-handles.sh <workbookId> [--verify] [--sql]
scripts/api/query-element.sh    <workbookId> <elementId> [csv|json]
```

`workbook-handles.sh` emits one manifest — url, pages, every element with its
**column ids**, optionally the generated SQL, and with `--verify` an actual row
count from a live export. That manifest is what you hand an agent or an API client
so it can address the dashboard. **Make it part of the deliverable**, not an
afterthought: a dashboard nobody can call is just a picture.

Notes:
- Export works on **any** element, including charts and KPI tiles —
  `visibleAsSource` is not required for it.
- An export round-trip is ~10s; budget for it in loops.

## ⚠ MCP sees a different org than your REST credentials

The Sigma MCP server may be bound to a **different tenant** than `SIGMA_BASE_URL`'s
credentials. Verified: REST creds resolve to org `8c99818a…` (`papercranestaging`)
while the MCP server returned only `sigma-on-sigma` content — MCP `describe` on a
just-created workbook returned *"No matching record"*, and REST returned
*"resource does not exist"* for a workbook MCP could see. Neither could see the
other's objects.

**This is a tenancy mismatch, not an indexing lag** — waiting does not fix it. It is
also the real reason connection ids differ between MCP and REST (previously filed as
a quirk). Check `GET /v2/whoami`.organizationId against the org slug in the MCP's own
result URLs before concluding a freshly-built workbook is broken. Consequence: for a
BYOD build, **resolve tables and verify results over REST**, not MCP. Also
`/mcp/v2` on the REST host returned 403 for a normal API token, so the
`mcp-*.sh` wrappers may not work with plain client-credentials auth.
