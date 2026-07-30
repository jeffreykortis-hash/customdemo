# Comparison KPI Card — Sigma plugin

A polished KPI card: current-vs-prior value with ▲/▼ RAG delta, a sparkline, and
a gradient background — the composite KPI look that's awkward to build with native
Sigma elements. Single-file, vanilla JS, `@sigmacomputing/plugin` SDK from CDN
(no build step). Renders synthetic data when opened standalone (preview).

**Hosted:** https://sigma-kpi-card-plugin-bb.netlify.app

## Use in Sigma
1. Admin → Plugins → Add plugin → paste the hosted URL.
2. In a workbook, add the plugin element; in the editor panel set: **source** element,
   **Trend order** (date/x column), **Measure**, **title**, **format** (currency/number/percent),
   **comparison** mode, **accent color**.

## Deploy updates
`netlify deploy --prod --dir . --site 476b3282-81e4-4993-b60b-f822129675c5`

## Fixed 2026-07-30

- **`dimension` was collected and never used.** `recompute()` ignored it, so the
  sparkline rendered in arbitrary source order and *both* comparison modes
  ("recent vs prior halves", "last vs first point") were computed over an
  unordered series. It now sorts by the dimension (numeric and date-like
  ascending; text preserves source order, since it has no meaningful order).
- **Currency was hardcoded to `$`.** Added `currency` (ISO 4217) and `locale`;
  a EUR or GBP card was previously impossible.
- **An increase was always green.** Added `direction`
  (Higher is better / Lower is better / Neutral). Without it, cost, churn,
  latency, defect rate and days-to-close all rendered backwards. The arrow still
  follows the number; only the colour follows whether that direction is good.
