#!/usr/bin/env python3
"""Shared library for synthetic star-schema generation. Not a CLI.

Every column generator is defined ONCE here, in two forms that must agree:
  * `sql(...)`  — the warehouse expression
  * `py(...)`   — the same computation in Python

That pairing is the whole point. Because nothing here uses an RNG, the Python
form reproduces the warehouse output EXACTLY, so `simulate-synth.py` isn't an
estimate — it is the data, available before anything is published. If the two
ever disagree, a non-deterministic term has leaked in.

DETERMINISM IS A HOUSE RULE, not a preference. `build_cohort_builder_reference.py`
puts the same SQL string on two element ids and requires them row-identical; any
RNG breaks that silently. `check_no_rng()` enforces it on emitted SQL.

DIALECT SURFACE IS DELIBERATELY TWO EXPRESSIONS. Everything else is arithmetic
common to Snowflake and Databricks. Verified 2026-07-30 executing on both through
Sigma's custom-SQL wrapping. Do not widen this surface without re-probing:
    row source        SEQ4()/GENERATOR   vs   range(0,N)
    day offset → date DATEADD            vs   DATE_ADD
Banned outright (breaks one dialect or the other): DATE_TRUNC, arrays, HASH(),
`::` casts, `||`, CURRENT_DATE(), PI().
"""
from __future__ import annotations

import json
import math
import re
from math import gcd

# Strides for decorrelating columns. 2 and 5 are excluded — they share factors
# with common row counts and collapse the distribution.
STRIDE_POOL = [7, 11, 13, 17, 19, 23, 29, 31, 37, 41, 43, 47, 53, 59, 61, 67, 71]

# Distinct irrational-ish multipliers for the noise term, so two measures never
# wobble in lockstep. Must not be rational multiples of pi.
NOISE_K = [0.7, 1.3, 1.7, 2.3, 2.9, 3.7, 4.3, 5.1]

# E|sin| = 2/pi and sd|sin| = sqrt(1/2 - 4/pi^2). Without recentring, a raw
# ABS(SIN()) noise term adds a systematic +64% to the mean — so a "6% noise"
# knob would silently move the level. These make amplitude mean what it says.
_ABS_SIN_MEAN = 2.0 / math.pi          # 0.63662
_ABS_SIN_SD = math.sqrt(0.5 - 4.0 / (math.pi ** 2))   # 0.30776

_RNG_DENYLIST = [
    "RANDOM", "RAND(", "UNIFORM(", "NORMAL(", "RANDSTR", "SHUFFLE",
    "CURRENT_DATE", "CURRENT_TIMESTAMP", "NOW(", "GETDATE", "LOCALTIMESTAMP",
    "UUID_STRING", "HASH(", "DATE_TRUNC", "ARRAY_CONSTRUCT",
]


def check_no_rng(sql: str) -> list[str]:
    """Anything here makes reruns non-identical or breaks one dialect."""
    up = sql.upper()
    return [t for t in _RNG_DENYLIST if t in up]


def coprime_stride(n: int, used: set[int]) -> int:
    """A stride coprime to n, so MOD(i*stride, n) is a PERMUTATION of 0..n-1.

    This is what makes weighted categoricals exact at any row count. The older
    fixed-modulus form MOD(i*stride, 1000) drifts badly below N=1000 — verified
    live: a requested 45/35/20 split came out 58/29/13 at N=120.
    """
    for s in STRIDE_POOL:
        if s not in used and gcd(s, n) == 1:
            used.add(s)
            return s
    for s in range(3, 10000, 2):          # fall back to any odd coprime
        if s not in used and gcd(s, n) == 1:
            used.add(s)
            return s
    raise ValueError(f"no stride coprime to {n}")


# --------------------------------------------------------------------------
# Dialects — the entire non-portable surface
# --------------------------------------------------------------------------
class Dialect:
    name = ""

    def row_source(self, n: int) -> str:
        raise NotImplementedError

    def date_from_days(self, anchor: str, days_expr: str) -> str:
        raise NotImplementedError


class Snowflake(Dialect):
    name = "snowflake"

    def row_source(self, n: int) -> str:
        return f"SELECT SEQ4() AS i FROM TABLE(GENERATOR(ROWCOUNT=>{n}))"

    def date_from_days(self, anchor: str, days_expr: str) -> str:
        return f"DATEADD('day', {days_expr}, DATE '{anchor}')"


class Databricks(Dialect):
    name = "databricks"

    def row_source(self, n: int) -> str:
        return f"SELECT id AS i FROM range(0, {n})"

    def date_from_days(self, anchor: str, days_expr: str) -> str:
        return f"DATE_ADD(DATE '{anchor}', {days_expr})"


DIALECTS = {"snowflake": Snowflake(), "databricks": Databricks()}


def dialect_for(conn_type: str) -> Dialect:
    d = DIALECTS.get((conn_type or "").strip().lower())
    if not d:
        raise ValueError(
            f"no SQL emitter for connection type {conn_type!r}. "
            f"Supported: {', '.join(sorted(DIALECTS))}. "
            "Generating rows from nothing needs a dialect-specific row source; "
            "there is no portable form.")
    return d


def sql_str(v) -> str:
    return "'" + str(v).replace("'", "''") + "'"


# --------------------------------------------------------------------------
# Column generators — sql() and py() must stay in lockstep
# --------------------------------------------------------------------------
def _weights(values: list[dict]) -> list[float]:
    ws = [float(v.get("weight", 0) or 0) for v in values]
    if sum(ws) <= 0:
        ws = [1.0] * len(values)
    total = sum(ws)
    return [w / total for w in ws]


def _cum_cuts(values: list[dict], n: int) -> list[int]:
    """Cumulative row-count cutoffs, so proportions land exactly."""
    ws, acc, cuts = _weights(values), 0.0, []
    for w in ws[:-1]:
        acc += w
        cuts.append(int(round(acc * n)))
    return cuts


def categorical_sql(col: dict, n: int, resid: str) -> str:
    vals, cuts = col["generator"]["values"], _cum_cuts(col["generator"]["values"], n)
    parts = [f"WHEN {resid} < {c} THEN {sql_str(v['value'])}" for c, v in zip(cuts, vals)]
    return "CASE " + " ".join(parts) + f" ELSE {sql_str(vals[-1]['value'])} END"


def categorical_py(col: dict, n: int, r: int):
    vals, cuts = col["generator"]["values"], _cum_cuts(col["generator"]["values"], n)
    for c, v in zip(cuts, vals):
        if r < c:
            return v["value"]
    return vals[-1]["value"]


def _noise_sql(amp: float, k: float) -> str:
    return (f"(1 + {amp} * ((ABS(SIN(i * {k})) - {_ABS_SIN_MEAN:.5f})"
            f" / {_ABS_SIN_SD:.5f}))")


def _noise_py(amp: float, k: float, i: int) -> float:
    return 1 + amp * ((abs(math.sin(i * k)) - _ABS_SIN_MEAN) / _ABS_SIN_SD)


def measure_terms(col: dict, day_expr: str, day_py):
    """Build the multiplicative factor list, in both forms.

    value = base x trend x annual x weekly x categoryEffect x noise,
    then floored and rounded. Multiplicative throughout so every knob reads as
    "a percentage of the level" regardless of `base`.
    """
    g = col["generator"]
    sql_parts: list[str] = [str(g.get("base", 1.0))]
    py_parts = [lambda i, row, _b=float(g.get("base", 1.0)): _b]

    for f in g.get("factors", []) or []:
        sql_parts.append(f["column"])
        py_parts.append(lambda i, row, c=f["column"]: float(row[c]))

    tr = g.get("trend")
    if tr:
        pct = float(tr.get("pctPerPeriod", 0))
        sql_parts.append(f"(1 + {pct} * {day_expr})")
        py_parts.append(lambda i, row, p=pct: 1 + p * day_py(i, row))

    for s in g.get("seasonality", []) or []:
        if s.get("kind") == "sine":
            amp = float(s.get("amplitude", 0.15))
            # 2*pi/periodDays baked at compile time — PI() is not portable.
            w = 2 * math.pi / float(s.get("periodDays", 365.25))
            ph = float(s.get("phase", 0))
            sql_parts.append(f"(1 + {amp} * SIN({w:.8f} * {day_expr} + {ph}))")
            py_parts.append(lambda i, row, a=amp, W=w, P=ph:
                            1 + a * math.sin(W * day_py(i, row) + P))
        elif s.get("kind") == "multiplier":
            by = {int(k): float(v) for k, v in (s.get("byDow") or {}).items()}
            if by:
                dow = f"MOD({day_expr} + {int(s.get('anchorDow', 0))}, 7)"
                whens = " ".join(f"WHEN {d} THEN {m}" for d, m in sorted(by.items()))
                sql_parts.append(f"(CASE {dow} {whens} ELSE 1.0 END)")
                py_parts.append(
                    lambda i, row, B=by, A=int(s.get("anchorDow", 0)):
                    B.get((day_py(i, row) + A) % 7, 1.0))

    ce = g.get("categoryEffects")
    if ce:
        col_name, mult = ce["column"], ce.get("multipliers", {})
        whens = " ".join(f"WHEN {sql_str(k)} THEN {v}" for k, v in mult.items())
        sql_parts.append(f"(CASE {col_name} {whens} ELSE 1.0 END)")
        py_parts.append(lambda i, row, C=col_name, M=mult: float(M.get(row.get(C), 1.0)))

    nz = g.get("noise")
    if nz:
        amp, k = float(nz.get("amplitude", 0.05)), float(nz.get("k", 1.3))
        sql_parts.append(_noise_sql(amp, k))
        py_parts.append(lambda i, row, a=amp, K=k: _noise_py(a, K, i))

    return sql_parts, py_parts


def wrap_round(expr: str, col: dict) -> str:
    g = col["generator"]
    out = expr
    if g.get("floor") is not None:
        out = f"GREATEST({g['floor']}, {out})"
    if g.get("cap") is not None:
        out = f"LEAST({g['cap']}, {out})"
    r = g.get("round")
    if r is not None:
        out = f"ROUND({out}, {int(r)})"
    return out


def wrap_round_py(v: float, col: dict):
    g = col["generator"]
    if g.get("floor") is not None:
        v = max(float(g["floor"]), v)
    if g.get("cap") is not None:
        v = min(float(g["cap"]), v)
    if g.get("round") is not None:
        v = round(v, int(g["round"]))
        if int(g["round"]) == 0:
            v = int(v)
    return v


def friendly(raw: str) -> str:
    """Warehouse column name -> Sigma's derived display name. Matches
    profile-table.py and build_byod_data_model.py exactly.

    Splits on `_` AND at letter<->digit boundaries:
    `q1_revenue` -> 'Q 1 Revenue', `icd10` -> 'Icd 10'."""
    return " ".join(p.capitalize()
                    for word in raw.split("_")
                    for p in re.findall(r"[A-Za-z]+|\d+", word))


def load_spec(path: str) -> dict:
    with open(path) as f:
        return json.load(f)


def table_by_name(spec: dict, name: str) -> dict | None:
    return next((t for t in spec.get("tables", []) if t["name"] == name), None)


def assign_strides(spec: dict) -> None:
    """Give every stride-driven column a persisted, reviewable stride."""
    for t in spec.get("tables", []):
        n, used = int(t.get("rows", 1)), set()
        for c in t.get("columns", []):
            g = c.get("generator", {})
            if g.get("kind") in ("categorical", "vocabulary", "boolean", "fk") \
                    and "stride" not in g:
                g["stride"] = coprime_stride(max(n, 2), used)
            # A null mask needs its OWN stride from the same pool, or the nulls
            # correlate with whichever column happens to share its stride.
            if c.get("nullRate") and "nullStride" not in c:
                c["nullStride"] = coprime_stride(max(n, 2), used)
        for ci, c in enumerate(t.get("columns", [])):
            g = c.get("generator", {})
            if g.get("kind") == "measure" and g.get("noise") and "k" not in g["noise"]:
                g["noise"]["k"] = NOISE_K[ci % len(NOISE_K)]
