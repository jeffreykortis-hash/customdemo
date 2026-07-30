# `_scaffold` — clone this to start a plugin

The reference implementation of the **JSON settings pattern** from
`sigma-plugin-patterns`. That pattern was documented in depth and implemented in
**none** of the nine plugins here; this closes that gap.

## Clone contract

Copy `index.html` into `plugins/<your-plugin>/` and change exactly three things:

1. **`BINDINGS`** — the columns your viz needs. Each entry's `name` is what the
   workbook spec binds to, as a **bare columnId string**
   (`config:{ source:{...}, value:"col-id" }` — the object form is rejected).
2. **`DEFAULT_SETTINGS.viz`** — your per-plugin visual options. Everything above
   `viz` is shared; leave it.
3. **`draw()` + the viz CSS** — your marks.

Leave the settings drawer, theme derivation, number formatting, direction
semantics, loading states and the ResizeObserver alone. They're the point.

## What you get for free

| | |
|---|---|
| **Rich config** | One `config` text field holds the whole settings object as JSON, so you aren't limited to the editor panel's control types. A drawer inside the plugin edits it, gated on the `editMode` toggle so viewers never see it. |
| **Forward compatibility** | `loadSettings()` deep-merges over `DEFAULT_SETTINGS`, so settings you add in a later version get their default in workbooks saved against an older one instead of coming back `undefined`. |
| **Brand-from-code** | The generator can emit the entire look at POST time: `"config": json.dumps(settings)` alongside the column bindings. No editor-panel clicking to brand a plugin. Treat **all** plugin config values as strings at the spec layer. |
| **Readable on any background** | Derives foreground from background luminance. |
| **Correct deltas** | `semantics.direction` supports `lower-is-better`, so cost, churn, latency and defect rate don't render a drop as bad. |
| **Right size, always** | ResizeObserver + `resize`, reading dimensions fresh in `draw()`. Sigma resizes the iframe *without* firing `window.resize`, so a resize listener alone isn't enough — this matters for any plugin that measures its container. |

## What the workbook does NOT give you

Per the SDK types (`@sigmacomputing/plugin@1.2.0`):

```ts
interface PluginStyle { backgroundColor: string }
```

**That is the entire style surface.** No palette, accent, font or text colour is
inherited. Don't design around workbook theme inheritance — take the background,
derive a foreground, and get everything else from settings. This asymmetry is
precisely why the settings JSON matters.

## Dev loop

```bash
cd plugins && python3 -m http.server 8080     # open http://localhost:8080/_scaffold/
python3 ../scripts/register_plugin.py "$SIGMA_BASE_URL" "$SIGMA_API_TOKEN" \
        "My Plugin" "http://localhost:8080/my-plugin/"
```

With no Sigma client present it renders synthetic rows, so you can iterate on the
visual before wiring it to a workbook. Always finish by checking it **inside** a
workbook at two different widths — the standalone preview can't catch a sizing bug.

## Regression test

```bash
npm i jsdom@29.1.1
curl -sL https://unpkg.com/react@18.3.1/umd/react.production.min.js > plugins/_scaffold/.react.js
curl -sL https://unpkg.com/@sigmacomputing/plugin@1.2.0            > plugins/_scaffold/.sdk.js
node plugins/_scaffold/test.js          # 24 assertions
```

It loads the **real** React + SDK bundles and drives startup the way the Sigma
host does. That matters more than it sounds: an earlier mock-based suite passed
25/25 while this plugin was completely broken in Sigma, five separate times —
because the mock injected `client` directly, hand-called the config callback, and
stubbed a `getElementData()` method that **does not exist in the SDK**. It
validated assumptions instead of behaviour.

So the stub here is a `Proxy` over the *genuine* client's key set that throws on
any property the real SDK lacks, and it asserts argument shapes (the element id
must be a **string**, not the `{kind, elementId}` config object). A stub must
never be more permissive than the thing it replaces.

The suite reproduces each historical failure if you revert the fix:

| Bug | Symptom in Sigma |
|---|---|
| Wrong SDK global (`SigmaPluginClient`) | silently renders synthetic data |
| Missing React peer dep | `window.SigmaPlugin` is an empty object, same silent fallback |
| `subscribe()` without `config.get()` | stuck on "select a source" on an already-configured workbook |
| Invented `getElementData()` | stuck on "Loading…" forever (throws synchronously, never reaches `.catch`) |
| Fatal message repainted by ResizeObserver | the real error is replaced by a benign placeholder |
