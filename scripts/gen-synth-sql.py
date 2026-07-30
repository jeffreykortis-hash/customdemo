#!/usr/bin/env python3
"""Compile a synthetic schema spec into warehouse SQL — one statement per table.

    scripts/gen-synth-sql.py <spec.json> --dialect snowflake|databricks
        [--table NAME] [--out DIR] [--compact]

Emits a four-stage CTE per table:

    g    -- the row source (the only dialect-specific FROM)
    ix   -- axis indices and stride residues, computed ONCE
    dims -- ids, dates, categoricals, booleans
    meas -- measures, which may reference dims columns by name
    ...  -- final projection: correlated columns, null masks, synthetic markers

The layering is not decoration. Referencing a column alias defined earlier in the
SAME select list ("lateral column alias") is not reliably portable — Snowflake
allows it, older Databricks runtimes don't. Layering removes the dependency, and
happens to make the emitted SQL readable, which matters because the SQL is itself
a reviewable deliverable here.

Nothing written is executed against the warehouse; the SQL fabricates rows at
query time. Every statement carries a SYNTHETIC header — see synthetic-labelling.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _synth as S  # noqa: E402


def header(spec: dict, table: dict, dialect: str) -> str:
    return (
        "-- =====================================================================\n"
        "-- SYNTHETIC DATA — fabricated at query time. NOT a measurement.\n"
        f"-- table: {table['name']} ({table.get('role','fact')}) · "
        f"{table.get('rows')} rows · {dialect}\n"
        f"-- spec:  {spec.get('name','?')} · generated {spec.get('generatedAt','?')} · "
        f"anchor {spec.get('anchorDate','?')}\n"
        "-- Deterministic: no RNG, no CURRENT_DATE. Reruns are row-identical.\n"
        "-- Nothing is written to the warehouse; these rows exist only in this query.\n"
        "-- =====================================================================")


def emit_table(spec: dict, table: dict, d: S.Dialect) -> str:
    n = int(table["rows"])
    cols = table["columns"]
    anchor = spec.get("anchorDate", "2024-01-01")

    # ---- ix: axis indices + one residue per stride-driven column -----------
    ix_parts = ["i"]
    date_days = None
    for c in cols:
        if c["generator"]["kind"] == "date":
            date_days = int(c["generator"].get("days", n))
            break
    # DAY_IX is always present: trend and seasonality reference it even when the
    # table has no date column of its own. CAST is unconditional because Spark's
    # integer division yields a double, and DATE_ADD wants an INT.
    ix_parts.append(f"CAST(MOD(i, {date_days or n}) AS INT) AS DAY_IX")
    for c in cols:
        g = c["generator"]
        if g.get("stride"):
            mod = int(g["dimRows"]) if g["kind"] == "fk" else n
            ix_parts.append(f"MOD(i * {g['stride']}, {mod}) AS R_{c['name']}")
        if c.get("nullRate"):
            ix_parts.append(f"MOD(i * {c['nullStride']}, {n}) AS NR_{c['name']}")

    # ---- dims: everything a measure might reference ------------------------
    dim_kinds = ("id", "sequence", "fk", "date", "categorical", "vocabulary", "boolean")
    dims_parts, meas_parts, corr_parts = [], [], []
    day_expr = "DAY_IX"

    for c in cols:
        g, name = c["generator"], c["name"]
        k = g["kind"]
        if k == "id":
            e = (f"CONCAT({S.sql_str(g.get('prefix',''))}, "
                 f"LPAD(CAST(i + {int(g.get('start',1))} AS STRING), "
                 f"{int(g.get('pad',6))}, '0'))")
        elif k == "sequence":
            e = f"i + {int(g.get('start', 1))}"
        elif k == "fk":
            # Cycling over the dimension's own row count guarantees referential
            # integrity by construction: every FK value exists in the dimension,
            # so there are no orphans and no join loss.
            e = f"MOD(i * {g['stride']}, {int(g['dimRows'])}) + 1"
        elif k == "date":
            e = d.date_from_days(anchor, day_expr)
        elif k in ("categorical", "vocabulary"):
            e = S.categorical_sql(c, n, f"R_{name}")
        elif k == "boolean":
            cut = int(round(float(g.get("probability", 0.5)) * n))
            e = f"CASE WHEN R_{name} < {cut} THEN TRUE ELSE FALSE END"
        elif k == "measure":
            parts, _ = S.measure_terms(c, day_expr, lambda i, row: 0)
            meas_parts.append(f"{S.wrap_round(' * '.join(parts), c)} AS {name}")
            continue
        elif k == "correlated":
            rate = g.get("rate", {})
            whens = " ".join(f"WHEN {S.sql_str(kk)} THEN {vv}"
                             for kk, vv in (rate.get("byValue") or {}).items())
            r_expr = f"(CASE {rate['column']} {whens} ELSE {rate.get('default',0.4)} END)" \
                     if whens else str(rate.get("default", 0.4))
            res = g.get("residual")
            noise = f" * {S._noise_sql(float(res['amplitude']), float(res['k']))}" if res else ""
            corr_parts.append(
                S.wrap_round(f"{g['of']} * {r_expr}{noise}", c) + f" AS {name}")
            continue
        else:
            raise ValueError(f"unknown generator kind {k!r} on {name}")

        if c.get("nullRate"):
            cut = int(round(float(c["nullRate"]) * n))
            e = f"CASE WHEN NR_{name} < {cut} THEN NULL ELSE {e} END"
        if k in dim_kinds:
            dims_parts.append(f"{e} AS {name}")

    order = [c["name"] for c in cols]
    # Marker columns survive someone copying this SQL out of Sigma entirely.
    note = (f"{spec.get('name', 'synthetic')} · generated "
            f"{spec.get('generatedAt', '?')} · fabricated, not measured")
    marker = [
        S.sql_str("SYNTHETIC") + " AS DATA_SOURCE_KIND",
        S.sql_str(note) + " AS SYNTHETIC_NOTE",
    ]

    nl = "\n       "
    sql = (
        f"WITH g AS (\n  {d.row_source(n)}\n),\n"
        f"ix AS (\n  SELECT {(',' + nl).join(ix_parts)}\n  FROM g\n),\n"
        f"dims AS (\n  SELECT ix.*,\n       {(',' + nl).join(dims_parts)}\n  FROM ix\n)"
    )
    if meas_parts:
        sql += f",\nmeas AS (\n  SELECT dims.*,\n       {(',' + nl).join(meas_parts)}\n  FROM dims\n)"
    src = "meas" if meas_parts else "dims"
    corr_names = [p.split(" AS ")[-1] for p in corr_parts]
    sel = [c for c in order if c not in corr_names] + corr_parts + marker
    sql += f"\nSELECT {(',' + nl).join(sel)}\nFROM {src}"
    return sql


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("spec")
    ap.add_argument("--dialect", choices=sorted(S.DIALECTS), required=True)
    ap.add_argument("--table")
    ap.add_argument("--out", help="directory to write <TABLE>.sql into")
    a = ap.parse_args()

    spec = S.load_spec(a.spec)
    S.assign_strides(spec)
    d = S.DIALECTS[a.dialect]

    tables = [t for t in spec["tables"] if not a.table or t["name"] == a.table]
    if not tables:
        sys.stderr.write(f"gen-synth-sql: no table named {a.table!r}\n")
        sys.exit(2)

    problems = 0
    for t in tables:
        sql = emit_table(spec, t, d)
        bad = S.check_no_rng(sql)
        if bad:
            # Determinism is enforced, not merely documented.
            sys.stderr.write(
                f"gen-synth-sql: {t['name']} emitted banned non-deterministic or "
                f"non-portable constructs: {', '.join(bad)}\n")
            problems += 1
            continue
        text = header(spec, t, a.dialect) + "\n" + sql
        if a.out:
            os.makedirs(a.out, exist_ok=True)
            p = os.path.join(a.out, f"{t['name']}.sql")
            with open(p, "w") as f:
                f.write(text + "\n")
            print(f"gen-synth-sql: wrote {p} ({len(text)} chars)")
        else:
            print(text + "\n")
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
