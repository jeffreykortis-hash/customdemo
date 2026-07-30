#!/usr/bin/env python3
"""Turn a profile-table.py profile + a role assignment into a Sigma DATA MODEL spec.

The canonical BYOD generator. Clone and adapt it — the mechanic doesn't change.

  # 1. profile the client's table
  python3 scripts/profile-table.py <conn> <DB> <SCHEMA> <TABLE> \
      --via workbook --folder <folderId> --out profile.json

  # 2. ask the human to confirm roles, then generate
  python3 examples/build_byod_data_model.py profile.json \
      --name "Acme Sales" --folder <folderId> \
      --date ORDER_DATE --grain ORDER_ID \
      --measures REVENUE,COST,QUANTITY \
      --dimensions REGION,CATEGORY,SEGMENT \
      --out datamodel-spec.json

  # 3. validate + publish + auto-verify
  scripts/api/publish-datamodel.sh post datamodel-spec.json

Defaults come from the profile's `candidates` when a flag is omitted, so the
zero-flag form works — but ASK THE HUMAN before trusting inference on their data.

WHY THERE IS NO SQL HERE. The source is `warehouse-table`, so all shaping is
Sigma formulas, which Sigma compiles to whichever dialect the connection speaks.
Verified identical on Snowflake and Databricks. Emitting warehouse SQL would
re-introduce the dialect problem this design exists to avoid.
"""
from __future__ import annotations

import argparse
import json
import sys


def friendly(raw: str) -> str:
    """Warehouse column name -> the display name Sigma derives from it.
    PRODUCT_TYPE -> 'Product Type'. Must match profile-table.py.
    """
    return " ".join(w.capitalize() if w.isalpha() else w for w in raw.split("_"))


def slug(raw: str) -> str:
    return "c-" + raw.lower().replace("_", "-")


def pick(explicit: str | None, candidates: list[dict], limit: int | None = None) -> list[str]:
    if explicit is not None:
        return [x.strip() for x in explicit.split(",") if x.strip()]
    names = [c["name"] for c in candidates]
    return names[:limit] if limit else names


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("profile", help="JSON from scripts/profile-table.py")
    ap.add_argument("--name", required=True, help="data model name")
    ap.add_argument("--folder", required=True, help="destination folderId")
    ap.add_argument("--date", help="date column (default: top candidate)")
    ap.add_argument("--grain", help="grain/entity key column (default: top candidate)")
    ap.add_argument("--measures", help="comma-separated (default: candidate measures)")
    ap.add_argument("--dimensions", help="comma-separated (default: top 8 candidates)")
    ap.add_argument("--element-name", help="element name = the formula prefix "
                                           "(default: the table name)")
    ap.add_argument("--period-anchor", choices=["max", "today", "none"], default="max",
                    help="how to tag Current Period vs Prior Year (default: max)")
    ap.add_argument("--out", default="datamodel-spec.json")
    a = ap.parse_args()

    prof = json.load(open(a.profile))
    src = prof["source"]
    cand = prof.get("candidates", {})
    by_name = {c["name"]: c for c in prof.get("columns", [])}

    date_col = a.date or (cand.get("dateColumn") or [{}])[0].get("name")
    grain_col = a.grain or (cand.get("grainKey") or [{}])[0].get("name")
    measures = pick(a.measures, cand.get("measures", []))
    dimensions = pick(a.dimensions, cand.get("dimensions", []), limit=8)

    unknown = [c for c in [date_col, grain_col, *measures, *dimensions]
               if c and c not in by_name]
    if unknown:
        sys.stderr.write(f"build-byod: not columns on this table: {unknown}\n")
        sys.exit(2)

    # The element's `name` is the LEFT side of every passthrough formula
    # ([<name>/<Friendly Col>]). Keep it equal to the table name unless the
    # caller overrides it.
    el_name = a.element_name or src["path"][-1]

    passthrough = [c for c in [date_col, grain_col, *measures, *dimensions] if c]
    seen, ordered = set(), []
    for c in passthrough:
        if c not in seen:
            seen.add(c)
            ordered.append(c)

    columns = [{
        "id": slug(c),
        "formula": f"[{el_name}/{friendly(c)}]",
        "name": friendly(c),
        **({"description": by_name[c]["description"]}
           if by_name.get(c, {}).get("description") else {}),
    } for c in ordered]

    # --- computed columns: the shaping, in Sigma formula syntax -------------
    if date_col:
        d = friendly(date_col)
        columns.append({"id": "c-month", "formula": f'DateTrunc("month", [{d}])',
                        "name": "Month"})

        if a.period_anchor != "none":
            if a.period_anchor == "today":
                # Self-maintaining, but yields an EMPTY current period on stale
                # data — profile-table.py warns with `stale-max-date`.
                anchor = "Today()"
                note = "Anchored to Today()."
            else:
                mx = str((by_name.get(date_col) or {}).get("max") or "")[:10]
                if not mx:
                    sys.stderr.write(
                        "build-byod: --period-anchor max needs a profiled max date; "
                        "re-run profile-table.py with --via workbook, or pass "
                        "--period-anchor today|none\n")
                    sys.exit(2)
                anchor = f'Date("{mx}")'
                note = (f"Anchored to the table's max date ({mx}) at build time — "
                        "regenerate when the data advances.")
            columns.append({
                "id": "c-period",
                "name": "Period Name",
                "formula": (
                    f'If([{d}] > DateAdd("year", -1, {anchor}), "Current Period", '
                    f'If([{d}] > DateAdd("year", -2, {anchor}), "Prior Year", Null))'
                ),
                "description": f"Rolling 12-month period tag. {note}",
            })

    # --- metrics: bare refs, may reference computed columns -----------------
    metrics = []
    for m in measures:
        fm = friendly(m)
        metrics.append({"id": f"m-{m.lower()}", "formula": f"Sum([{fm}])",
                        "name": f"Total {fm}"})
    if grain_col:
        fg = friendly(grain_col)
        metrics.append({"id": "m-count", "formula": f"CountDistinct([{fg}])",
                        "name": f"{fg} Count"})

    # One timeline metric gives period-over-period comparison in the semantic
    # layer. NOTE: `comparison.direction` is REJECTED by the API — only
    # `comparisonPeriod` is accepted.
    if metrics and date_col:
        metrics[0]["timeline"] = {
            "dateColumnId": slug(date_col),
            "truncation": "month",
            "comparison": {"comparisonPeriod": "year"},
        }

    spec = {
        "name": a.name,
        "folderId": a.folder,
        "schemaVersion": 1,
        "pages": [{"id": "page-1", "name": "Model", "elements": [{
            "id": "tbl-" + el_name.lower().replace("_", "-"),
            "kind": "table",
            "name": el_name,
            "source": {
                "connectionId": src["connectionId"],
                "kind": "warehouse-table",
                "path": src["path"],
            },
            "columns": columns,
            "metrics": metrics,
            "order": [c["id"] for c in columns],
        }]}],
    }

    with open(a.out, "w") as f:
        json.dump(spec, f, indent=2)
    print(f"build-byod: wrote {a.out}")
    print(f"  element   {el_name}  (formula prefix)")
    print(f"  columns   {len(columns)} ({len(ordered)} passthrough, "
          f"{len(columns) - len(ordered)} computed)")
    print(f"  metrics   {len(metrics)}")
    print(f"  next      scripts/api/publish-datamodel.sh post {a.out}")


if __name__ == "__main__":
    main()
