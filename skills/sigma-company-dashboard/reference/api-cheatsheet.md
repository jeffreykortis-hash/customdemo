# Sigma workbooks-as-code — verified API cheatsheet

Hard-won from real builds. **Trust GET-back specs of recent, UI-built workbooks
over any older doc/exemplar — the spec schema has drifted.** POST to
`/v2/workbooks/spec`; `folderId` is REQUIRED at CREATE.

## The masked "Invalid kind" error
`{"message":"pages[0].elements[N]: Invalid kind: \"<kind>\""}` almost never means
the kind is unsupported — it means a **field on that element has the wrong shape**.
Bisect by element index; compare the offending element to a GET-back exemplar.

## Verified element shapes (CURRENT schema)
- **table (from custom SQL):** `{kind:"table", source:{connectionId, statement, kind:"sql"}, columns:[{id, formula:"[Custom SQL/<OUT>]", name}], name, order:[...]}`. A custom-SQL table MUST declare `columns` — omitting them is the masked `Invalid kind: "table"`.
- **table (from a warehouse table, DIALECT-FREE):** `{kind:"table", source:{connectionId, kind:"warehouse-table", path:[DB,SCHEMA,TABLE]}, columns:[{id, formula:"[<TABLE_NAME>/<Friendly Col>]", name}], ...}`. Friendly name = `PRODUCT_TYPE` → `Product Type`. No SQL, so the same spec works on Snowflake and Databricks.
- **table (from a DATA MODEL):** `{kind:"table", source:{kind:"data-model", dataModelId, elementId}, columns:[{id, formula:"[<ElementName>/<Column Name>]", name}], ...}`. Prefer this for BYO client data — see `sigma-byod-data-model`.
- **kpi-chart:** `{kind:"kpi-chart", source:{elementId, kind:"table"}, columns:[{id, formula, name, format?}], value:{columnId, color}, name:{visibility:"hidden"} | {text,fontWeight,fontSize}, layout:{anchor:"middle"}, style?}`. Comparative KPI: `timeline:{columnId}` + `periodComparison:"month"`. **Encoding uses `columnId`, NOT `id`** (old `value:{id}` → masked Invalid kind).
- **bar/line/area:** the `kind` is **`bar-chart`** / `line-chart` / `area-chart` / `combo-chart` / `donut-chart` / `pie-chart` / `scatter-chart` — bare `"bar"` is the masked `Invalid kind: "bar"`. `xAxis:{columnId, sort?, format?}`, `yAxis:{columnIds:[...], format?}` (an OBJECT with `columnIds`, not `[{id}]` — the `[{id}]` form in `sigma-workbook-conventions/examples/*.json` is an older GET-back; the `columnIds` object form is verified working 2026-07-30).
- **⚠ A chart/KPI sourced from a table must REDECLARE every field its aggregate touches.** `Sum([Price] * [Quantity])` fails with `Unknown column "[Price]"` unless that element also declares passthrough columns for Price and Quantity. And the passthrough prefix is the **immediate source element's `name`**, not the underlying data model's — table `{name:"Sales"}` → the chart uses `[Sales/Price]`, even when `Sales` itself sources a data model whose element is named something else. Keep table names short and free of punctuation for this reason. `name`/`legend` accept `{visibility:"hidden"}`. line: `lineAreaStyle:{interpolation:"monotone"}`.
- **series/bar color:** `color:{by:"category", column:<COL-ID>, scheme:[...]}`. `column` must be a SEPARATE column (can't reuse the x/y column). Uniform-color bars → add a duplicate dimension column and color by it (scheme one color) + hide legend.
- **single-line color:** no per-line override — comes only from `themeOverrides.categoricalScheme[0]`.
- **region-map:** `{kind:"region-map", source:{elementId,kind:"table"}, columns:[{id,formula},...], region:{id:<stateColId>, regionType:"us-state"}, color:{by:"scale", column:<metricColId>}}`.
- **pivot-table:** `rowsBy:[{id}]`, `columnsBy:[{id}]`, `values:["<colId>"]` (exact — objects-as-values rejected).
- **container:** `{kind:"container", style?, backgroundImage?}`. Its children are placed INSIDE its `<GridContainer>` in the layout XML.
- **image:** `{kind:"image", url:"<https or data-URI>", style:{fit:"cover"|"scale-down"}}`.
- **text:** `{kind:"text", body:"<markdown, supports {{formula}} incl CallText>", verticalAlign:"middle"}`.
- **control:** `{kind:"control", controlId (workbook-unique), controlType:"list"|"date-range"|"text-area"|..., filters:[{source:{kind:"table",elementId},columnId}], source:{kind:"source",source:{...},columnId}}`.
- **plugin (needs a registered pluginId):** `{kind:"plugin", pluginId, config:{source:{kind:"element",elementId}, <binding>:"<columnId>"}}`. VERIFIED: each column binding is a **BARE columnId string**, not an object — the `{kind:"column",columnId,source}` object form is REJECTED (masked as `Invalid kind:"plugin"`). Binding keys must match the plugin's `configureEditorPanel` variable names. Register a plugin from code via `POST /v2/plugins {name,description,url,type:"element"}` → returns `pluginId` (no admin UI needed). List with `GET /v2/plugins`.

## style vocabulary (rounds-trips on containers/kpi/chart/image)
`backgroundColor` (hex or `{kind:"theme",ref:"colors-..."}`), `borderColor`,
`borderWidth` (0/1/3), `borderRadius` (`"pill"|"round"|"square"`), `padding` (only
`"none"`), `backgroundImage` (top-level, `{url, style:{fit}}`), `fit`, `color`,
`strokeStyle`, `textWrap`, `align`, `bold`, `fontSize`/`fontWeight` (on kpi/chart `name`).

## Column format (POSTS FINE — the "format is rejected" doc is stale)
Currency `{"kind":"number","formatString":"$.3~s","currencySymbol":"$","decimalSymbol":".","digitGroupingSymbol":",","digitGroupingSize":[3]}`;
percent `{"kind":"number","formatString":".1%"}`; datetime axis `{"kind":"datetime","formatString":"%b %Y"}`.

## Layout
Top-level `layout` XML string; one `<Page>` per page (multiple `<Page>` siblings =
tabs). Every element `id` must appear as a `LayoutElement`/`GridContainer` in it,
and every `container` needs a matching `<GridContainer>` WITH nested children.
`<Page type="grid" gridTemplateColumns="repeat(24,1fr)" ...>`. **Cross-page
element sourcing works** (a chart on page A can source a table on page B).

## The big gotchas
- **Theme:** full `themeOverrides` reference (all keys verified round-tripping) is
  in `sigma-workbook-styling` — the terse duplicates below are the ones that bite
  most often during a command-center build.
- **Text color = theme, not element.** `style.color` on text (and the kpi `name`)
  is ignored → renders `themeOverrides.colors.text`. White text on a dark surface
  must be a **data-URI SVG image**; a colored callout must be a **light-tint
  container** (dark theme-text reads). Dark box + text = invisible.
- **Dark canvas breaks control dropdowns** (white popup + light theme-text). Use a
  LIGHT canvas + dark accent cards (hero, gradient KPI cards, plugin panel).
- **Sparklines:** stable metrics render flat unless the y-axis auto-fits →
  `yAxis.format.scale = {type:"linear", zero:false, hideZeroLine:true}`. Give each
  KPI card its OWN trend formula (don't reuse revenue for all).
- `verticalAlign` on text: only `"middle"` (top/bottom → masked Invalid kind).
- **UI-only (NOT spec-able), even after enabling in the UI:** `chat` element and
  `tabbed-container` — the API rejects both. Use a styled placeholder + pages-as-tabs.
- Composite KPI card = a gradient `container` (backgroundImage) holding: a white
  SVG title image, "Current/Prior" white SVG label images, two transparent
  `kpi-chart`s (`value.color:"#fff"`, `style.backgroundColor:"transparent"`), and a
  transparent sparkline line-chart. All children nested in the container's GridContainer.

## Auth / hosting
Token via `scripts/get-token-staging.sh` (client_credentials → bearer); clear
`/tmp/.sigma_token` when switching creds. Netlify CLI authed; create a UNIQUE
site then deploy with an explicit `--site`.

## Data models as code (verified 2026-07-30, papercranestaging, Snowflake + Databricks)
`scripts/api/publish-datamodel.sh post|put|get-spec|verify` wraps
`/v2/dataModels/spec`. Driven by the **`sigma-byod-data-model`** skill.
- **Responses are JSON**, not YAML: `{"success":true,"dataModelId":"..."}`. The
  workbook endpoint returns `{"success":true,"workbookId":"..."}` too — an older
  note in this repo claiming YAML is stale.
- **Submitted ids are PRESERVED**, not remapped. Reference them immediately.
- **Column ids are arbitrary.** UI-built models show `inode-<slug>/<COL>`, which
  matches neither the table's `inodeId` nor its URL slug. `c-price` works fine.
- Passthrough column → `[<ElementName>/<Friendly Name>]`. Computed columns and
  **all metrics** use BARE sibling refs (`[Price] - [Cost]`,
  `Sum([Unit Margin] * [Quantity])`). Metrics may reference computed columns.
- `metrics[].timeline` = `{dateColumnId, truncation, comparison:{comparisonPeriod}}`
  and round-trips exactly. **`comparison.direction` is REJECTED.**
- `format` on a data-model column/metric is rejected (`Missing "kind" field`).
- **HTTP 200 proves almost nothing here.** A formula referencing a nonexistent
  column is ACCEPTED and becomes a column of type `error`, visible only via
  `mcp-describe.sh datamodel-element`. Duplicate column names are accepted and
  the second is silently renamed `Name (1)`. `scripts/validate-datamodel-spec.py`
  catches both pre-POST and `publish-datamodel.sh` runs it plus an auto-verify.
- `/v2/files` type filter is **`data-model`** (hyphenated); legacy uploads are
  type `dataset`. A model sourced from an uploaded CSV can't `get-spec` at all.

## Plugin config from the spec (verified 2026-07-30)
Brand a plugin entirely from the generator — no editor-panel clicking:
```python
{"kind":"plugin","pluginId":PID,"config":{
   "source":{"kind":"element","elementId":"tbl"},
   "label":"c-label", "value":"c-value",          # BARE columnId strings
   "config": json.dumps(settings),                 # whole look as one JSON string
   "editMode":"false"}}                            # note: the STRING "false"
```
Verified round-tripping POST → GET **byte-identical**, including a ~200-char
escaped JSON string: `pluginId`, the bare column bindings, the `source` object,
`editMode` as a string, and the settings JSON (which re-parses to the same
object). Treat **all** plugin config values as strings at the spec layer.
Reference implementation: `plugins/_scaffold/`.

## Writeback (verified 2026-07-30)
`/v2/connections` exposes **`writeAccess`** (`true`/`null`) and **`writebacks`**
(`[{database, schema}]`). Input tables, warehouse views, materialization and CSV
upload all require write access; reads do not — so a read-only connection yields
a workbook whose charts work and whose input tables silently do nothing. Use
`scripts/api/list-connections.sh --writable`. It's an Admin-only toggle
(Administration → Connections → Enable write access + a destination): Snowflake
takes a schema, Databricks a catalog + schema. Only 16 of 52 connections in the
staging org qualified.

## More gotchas (verified 2026-07-24, demeng, scatter-lasso plugin build)
- **Cloudflare WAF blocks any JSON key CONTAINING the substring "field"** (case-sensitive,
  e.g. `filterField`) on `POST/PUT .../spec` — returns an HTML "Attention Required! |
  Cloudflare" block page, NOT a Sigma API error, so it looks like a hang/wrong-endpoint
  rather than a 400. Confirmed by bisection: `filterField`/`passField`/bare `field` all
  blocked; renaming to `filterColumn` fixed it instantly. If a spec POST/PUT returns an
  HTML Cloudflare page instead of JSON/YAML, suspect a flagged key name first — bisect
  key names, not just value shapes. (A plain Python `urllib` User-Agent was NOT the
  cause, ruled out first.)
- **POST/PUT `/v2/workbooks/spec` responses are YAML, not JSON** (`success: true` /
  `workbookId: ...`) — a `json.loads()`-only parser throws `JSONDecodeError` on a
  successful call, which looks exactly like a crash. Always fall back to printing the
  raw text (or `yaml.safe_load`) instead of assuming JSON — otherwise you'll think a
  successful POST failed and re-POST, creating duplicate workbooks.
- **A `dropdown`-type plugin config value must be POSTed as a STRING even when the
  declared `values` are numbers** (e.g. `pointSize: 2` → masked `Invalid kind:"plugin"`;
  `pointSize: "2"` → posts fine, and the plugin's own `Number(cfg.pointSize)` coerces it
  back). This matches the existing "column bindings are bare id strings" rule — treat
  ALL plugin `config` values as strings at the spec layer, not just column/control ids.
- **`visibleAsSource:false` on a table element does NOT hide it from a page's layout.**
  If you omit its `LayoutElement`, Sigma auto-appends one at the bottom of whichever
  page it's declared on instead of leaving it off-page — a raw 100K-row SQL source
  meant only to feed a plugin + a filtered child table showed up as its own giant table
  block. Fix: put backing/helper source tables on a SEPARATE page with top-level
  `"visibility": "hidden"` on that page (cross-page element sourcing still works fine).
- **Deleting a workbook created by mistake:** `DELETE /v2/workbooks/{id}` 404s — use
  `DELETE /v2/files/{id}` instead (workbooks live in the shared file-tree namespace).
- **Plugin registration (`POST /v2/plugins`) can return a masked HTTP 404** even though
  the plugin registers successfully server-side — always confirm with `GET /v2/plugins`
  (search by name) and use the pluginId from there rather than trusting a non-200 as a
  hard failure.
- Full worked example (100K-point canvas scatterplot plugin with rectangle-brush
  selection driving a `list` control that filters a child table): plugin source
  `plugins/scatter-lasso-select/`, generator pattern per above. The plugin→control
  binding IS spec-able: `config.<variableFieldName>: "<controlId>"` as a bare string,
  exactly like column bindings — no manual UI bind step needed.

## Tabbed containers (verified working, 2026-07-24 — corrects an older "UI-only" claim)
`kind:"tabbed-container"` JSON element `{id,kind,tabs:[{name},...],tabBar:{alignment}}`
— `tabs[]` items are LABELS ONLY. The layout XML wraps it with a `<TabbedContainer
elementId=... type="tabbed-container" gridColumn=... gridRow=...>` containing N
`<Tab gridTemplateColumns="repeat(24,1fr)" gridTemplateRows="auto">` children IN
ORDER (matched by position — no name attribute), each a mini-grid that can nest
`<LayoutElement>` children. Use this for a command-center's left column (chart /
plugin / detail-tables tabs) or a cohort-builder's Builder/Visualize split — see
`sigma-cohort-builder-app`.
- **⚠ Never nest a `<GridContainer>` inside a `<Tab>`** — it scrambles render order
  (elements can render wildly out of declared order with large gaps, even though
  POST/PUT accepts it, masked as always). Only bare `<LayoutElement>` children of a
  `<Tab>` are verified to render in order. Apply `style.backgroundColor` etc. directly
  on the leaf element instead of wrapping it in a container.
- **`style.padding` only accepts the literal `"none"` or must be omitted** — any other
  value is rejected.
- **A `list` control has NO code-representable default/initial value** — both
  `defaultValue` and `value` (as a formula) silently vanish on GET-back. If a picker
  needs to "start on the most recent row," the only real lever is firing
  `set-control-value` from whatever action creates that row (e.g. right after an
  `insert-rows` Save) — there's no way to default it on first page load from spec alone.
- **Grouped-table `sort[].direction` enum is `"ascending"`/`"descending"`, NOT
  `"asc"`/`"desc"`** — the abbreviated form POSTs "successfully" but the whole `sort`
  key silently vanishes on GET-back. Separately, a PLAIN (non-grouped) `table`'s
  `sort`/`limit`/`sorts`/`orderBy`/`sortColumns`/`defaultSort` fields are ALL silently
  dropped regardless of spelling — `groupings:[{groupBy,calculations,sort:[{columnId,
  direction:"descending"}]}]` (grouping by every displayed dim at the SAME grain as
  the raw data, no real aggregation) is the only real lever for a default-sorted /
  "Top N" table from code.
