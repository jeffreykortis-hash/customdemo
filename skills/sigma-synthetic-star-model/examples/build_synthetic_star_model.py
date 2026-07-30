#!/usr/bin/env python3
"""Schema spec -> a publishable Sigma DATA MODEL with a star schema.

The canonical generator for `sigma-synthetic-star-model`. Clone and adapt it.

    python3 scripts/gen-synth-sql.py spec.json --dialect snowflake --out ./sql
    python3 examples/build_synthetic_star_model.py spec.json \
        --connection <connId> --folder <folderId> --sql-dir ./sql \
        --out datamodel-spec.json
    scripts/api/publish-datamodel.sh post datamodel-spec.json

Produces ONE data model containing N elements — a fact plus its dimensions —
each with a `source:{kind:"sql"}`, wired together by real `relationships`.
Verified 2026-07-30: multi-element models, sql sources and relationships all
round-trip, and a cross-element JOIN returns correct rows.

TWO RULES THAT ARE NOT STYLE:

1. **Column ids are prefixed per element** (`f-`, `d0-`, `d1-`). Column ids are
   NOT unique across elements in Sigma — a live 16-element model reuses the same
   id on two elements — and relationship keys resolve BY ID. An unprefixed id can
   silently join the wrong table and return plausible rows.

2. **The fact carries denormalized copies of the dimension attributes** the
   dashboard slices by. Workbooks source the FACT and reference only its own
   columns, so no dashboard element depends on a cross-element resolution. The
   dimensions and relationships still exist for the semantic layer and for UI
   exploration — this just keeps the dashboard off an unverified path.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "..", "..", "scripts"))
import _synth as S  # noqa: E402

# Marker columns the emitter appends to every generated statement.
MARKERS = ["DATA_SOURCE_KIND", "SYNTHETIC_NOTE"]


def el_id(table: dict, idx: int) -> str:
    if table.get("role") == "fact":
        return "el-fact"
    # Strip a conventional DIM_/D_ prefix so DIM_STORE doesn't become
    # "el-dim-dim-store".
    base = table["name"].lower()
    for p in ("dim_", "d_"):
        if base.startswith(p):
            base = base[len(p):]
            break
    return "el-dim-" + base.replace("_", "-")


def col_prefix(table: dict, idx: int) -> str:
    return "f-" if table.get("role") == "fact" else f"d{idx}-"


def build(spec: dict, conn: str, folder: str, sql_by_table: dict,
          name: str | None) -> dict:
    tables = spec["tables"]
    fact_i = next((i for i, t in enumerate(tables) if t.get("role") == "fact"), 0)
    elements = []

    for i, t in enumerate(tables):
        pfx = col_prefix(t, i)
        cols = []
        for c in t["columns"] + [{"name": m, "type": "string"} for m in MARKERS]:
            cid = pfx + c["name"].lower().replace("_", "-")
            entry = {
                "id": cid,
                "formula": f"[Custom SQL/{c['name']}]",
                "name": S.friendly(c["name"]),
                # Layer 5 of the labelling: column descriptions surface as UI
                # tooltips AND in the describe DDL, which is what the NEXT agent
                # reads before citing these numbers as fact.
                "description": "SYNTHETIC: " + _describe(c),
            }
            cols.append(entry)

        el = {
            "id": el_id(t, i),
            "kind": "table",
            "name": S.friendly(t["name"]),
            "source": {"connectionId": conn, "kind": "sql",
                       "statement": sql_by_table[t["name"]]},
            "columns": cols,
            "order": [c["id"] for c in cols],
        }

        if i == fact_i:
            # Month + Period Name make the model consumable by the dashboard
            # generator's comparative KPI cards without any further authoring.
            # The period anchor is the spec's own max date, computed here — never
            # Today(), which yields an EMPTY current period on a fixed window.
            date_col = next((c for c in t["columns"]
                             if c["generator"]["kind"] == "date"), None)
            if date_col:
                dn = S.friendly(date_col["name"])
                mx = _max_date(spec, date_col)
                cols.append({"id": pfx + "month", "name": "Month",
                             "formula": f'DateTrunc("month", [{dn}])',
                             "description": "SYNTHETIC: month truncation of " + dn})
                cols.append({
                    "id": pfx + "period", "name": "Period Name",
                    "formula": (f'If([{dn}] > DateAdd("year", -1, Date("{mx}")), '
                                f'"Current Period", If([{dn}] > DateAdd("year", -2, '
                                f'Date("{mx}")), "Prior Year", Null))'),
                    "description": (f"SYNTHETIC: rolling 12-month period tag anchored "
                                    f"to the generated max date {mx}.")})
                el["order"] = [c["id"] for c in cols]
            el["metrics"] = _fact_metrics(t, pfx)
            rels = []
            for j, dt in enumerate(tables):
                if j == fact_i:
                    continue
                fk = next((c for c in t["columns"]
                           if c["generator"].get("kind") == "fk"
                           and c["generator"].get("table") == dt["name"]), None)
                if not fk or not dt.get("primaryKey"):
                    continue
                rels.append({
                    "id": f"rel-{el_id(t, i)}-{el_id(dt, j)}",
                    "name": f"{S.friendly(t['name'])} → {S.friendly(dt['name'])}",
                    "targetElementId": el_id(dt, j),
                    "keys": [{
                        "sourceColumnId": pfx + fk["name"].lower().replace("_", "-"),
                        "targetColumnId": col_prefix(dt, j)
                        + dt["primaryKey"].lower().replace("_", "-"),
                    }],
                })
            if rels:
                el["relationships"] = rels
        elements.append(el)

    model_name = (name or spec.get("name", "Synthetic")) + " (SYNTHETIC)"
    return {
        "name": model_name,
        # Layers 2 and 3 of the labelling.
        "description": (
            f"SYNTHETIC / FABRICATED DATA — generated {spec.get('generatedAt','?')} "
            f"by sigma-synthetic-star-model from spec '{spec.get('name','?')}'. "
            "Every value is computed by formula at query time; nothing was measured "
            "and nothing is stored in the warehouse. Do not use for decisions."),
        "folderId": folder,
        "schemaVersion": 1,
        "pages": [{"id": "page-1", "name": "Model", "elements": elements}],
    }


def _max_date(spec: dict, date_col: dict) -> str:
    """The last date the generator will actually emit: anchor + days - 1.

    Known exactly because generation is deterministic, so the period tag needs
    no warehouse query and can't drift from the data.
    """
    import datetime as _dt
    anchor = _dt.date.fromisoformat(spec.get("anchorDate", "2024-01-01"))
    days = int(date_col["generator"].get("days", 365))
    return (anchor + _dt.timedelta(days=max(days - 1, 0))).isoformat()


def _describe(c: dict) -> str:
    g = c.get("generator") or {}
    k = g.get("kind")
    if k == "measure":
        bits = [f"base {g.get('base')}"]
        if g.get("trend"):
            bits.append(f"{g['trend'].get('pctPerPeriod',0)*100:.3f}%/day trend")
        for s in g.get("seasonality", []) or []:
            bits.append(f"{s.get('kind')} seasonality"
                        + (f" {s.get('amplitude')}" if s.get("amplitude") else ""))
        if g.get("categoryEffects"):
            bits.append("varies by " + g["categoryEffects"]["column"])
        if g.get("noise"):
            bits.append(f"{g['noise'].get('amplitude')} noise")
        return "fabricated measure — " + ", ".join(bits)
    if k == "correlated":
        return f"fabricated — derived from {g.get('of')} at a per-category rate"
    if k == "fk":
        return f"fabricated foreign key -> {g.get('table')}.{g.get('column')}"
    if k in ("categorical", "vocabulary"):
        return "fabricated category"
    if k in ("id", "sequence"):
        return "fabricated identifier"
    if k == "date":
        return f"fabricated date over {g.get('days')} days"
    return "fabricated value / provenance marker"


def _fact_metrics(t: dict, pfx: str) -> list[dict]:
    """Sum every measure, count the grain, and put ONE timeline comparison on the
    first measure. `comparison.direction` is rejected by the API — only
    `comparisonPeriod` is accepted."""
    metrics, date_col = [], None
    for c in t["columns"]:
        if c["generator"]["kind"] == "date" and not date_col:
            date_col = pfx + c["name"].lower().replace("_", "-")
    for c in t["columns"]:
        k = c["generator"]["kind"]
        if k in ("measure", "correlated"):
            fname = S.friendly(c["name"])
            metrics.append({"id": "m-" + c["name"].lower().replace("_", "-"),
                            "formula": f"Sum([{fname}])", "name": f"Total {fname}"})
    grain = next((c for c in t["columns"] if c["generator"]["kind"] == "id"), None)
    if grain:
        metrics.append({"id": "m-count",
                        "formula": f"CountDistinct([{S.friendly(grain['name'])}])",
                        "name": f"{S.friendly(grain['name'])} Count"})
    if metrics and date_col:
        metrics[0]["timeline"] = {"dateColumnId": date_col, "truncation": "month",
                                  "comparison": {"comparisonPeriod": "year"}}
    return metrics


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("spec")
    ap.add_argument("--connection", required=True)
    ap.add_argument("--folder", required=True)
    ap.add_argument("--sql-dir", required=True, help="output dir of gen-synth-sql.py")
    ap.add_argument("--name")
    ap.add_argument("--out", default="datamodel-spec.json")
    a = ap.parse_args()

    spec = S.load_spec(a.spec)
    S.assign_strides(spec)

    sql_by_table = {}
    for t in spec["tables"]:
        p = os.path.join(a.sql_dir, f"{t['name']}.sql")
        if not os.path.exists(p):
            sys.stderr.write(f"build-star: missing {p} — run gen-synth-sql.py first\n")
            sys.exit(2)
        sql_by_table[t["name"]] = open(p).read().strip()

    model = build(spec, a.connection, a.folder, sql_by_table, a.name)
    with open(a.out, "w") as f:
        json.dump(model, f, indent=2)

    els = model["pages"][0]["elements"]
    rels = sum(len(e.get("relationships", [])) for e in els)
    print(f"build-star: wrote {a.out}")
    print(f"  model     {model['name']}")
    print(f"  elements  {len(els)} ({', '.join(e['id'] for e in els)})")
    print(f"  relations {rels}")
    print(f"  next      scripts/api/publish-datamodel.sh post {a.out}")


if __name__ == "__main__":
    main()
